"""
Conexão da Central com o TikTok (Content Posting API — modo rascunho / "inbox").

O vídeo vai pra caixa de entrada do app do TikTok da Juliane como rascunho. Ela abre a
notificação, confere, escolhe o produto do TikTok Shop e publica. Nada é publicado direto.

Fluxo:
  1. link_autorizacao()  → ela abre, faz login no TikTok e autoriza o app
  2. a página conectar.html (no domínio dela) mostra o código → ela manda /tiktok_codigo <código>
  3. trocar_codigo()     → guarda access_token (24h) e refresh_token (1 ano) em tiktok.json
  4. enviar_rascunho()   → init + upload do arquivo; status() acompanha

Configuração (variáveis de ambiente, em /etc/central-tiktok.env):
  TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI
Docs: https://developers.tiktok.com/doc/content-posting-api-get-started-upload-content/
"""
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI", "").strip()
ESCOPOS = "user.info.basic,video.upload"

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"


class ErroTikTok(Exception):
    def __init__(self, codigo, mensagem):
        super().__init__(f"{codigo}: {mensagem}")
        self.codigo = codigo


def configurado():
    return bool(CLIENT_KEY and CLIENT_SECRET and REDIRECT_URI)


def _req(url, dados=None, cabecalhos=None, metodo=None, form=False):
    cab = dict(cabecalhos or {})
    corpo = None
    if dados is not None:
        if form:
            corpo = urllib.parse.urlencode(dados).encode()
            cab["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            corpo = json.dumps(dados).encode()
            cab["Content-Type"] = "application/json; charset=UTF-8"
    req = urllib.request.Request(url, data=corpo, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as ex:
        try:
            return json.load(ex)
        except Exception:
            raise ErroTikTok(f"http_{ex.code}", ex.reason)


def _checar(resp):
    """As respostas da API v2 trazem {"data": ..., "error": {"code": "ok", ...}}."""
    erro = resp.get("error") or {}
    if erro.get("code", "ok") != "ok":
        raise ErroTikTok(erro.get("code"), erro.get("message", ""))
    if "access_token" not in resp and "data" not in resp and resp.get("error"):
        raise ErroTikTok(resp.get("error"), resp.get("error_description", ""))
    return resp.get("data", resp)


# ------------------------------------------------------------------ autorização
class Conta:
    """Guarda os tokens num arquivo (chmod 600) e renova sozinho."""

    def __init__(self, arquivo):
        self.arquivo = Path(arquivo)

    def _ler(self):
        try:
            return json.loads(self.arquivo.read_text())
        except Exception:
            return {}

    def _salvar(self, d):
        self.arquivo.write_text(json.dumps(d))
        os.chmod(self.arquivo, 0o600)

    def conectada(self):
        d = self._ler()
        return bool(d.get("refresh_token")) and d.get("refresh_expira", 0) > time.time()

    def nome(self):
        return self._ler().get("nome")

    def link_autorizacao(self):
        estado = secrets.token_urlsafe(16)
        d = self._ler()
        d["estado"] = estado
        self._salvar(d)
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_key": CLIENT_KEY, "response_type": "code", "scope": ESCOPOS,
            "redirect_uri": REDIRECT_URI, "state": estado})

    def _guardar_tokens(self, t):
        d = self._ler()
        d.update(access_token=t["access_token"], refresh_token=t["refresh_token"],
                 expira=time.time() + int(t.get("expires_in", 86400)) - 300,
                 refresh_expira=time.time() + int(t.get("refresh_expires_in", 31536000)) - 3600,
                 open_id=t.get("open_id"), escopos=t.get("scope", ""))
        self._salvar(d)

    def trocar_codigo(self, codigo):
        """Recebe o código (ou a URL inteira que a página mostrou) e conecta a conta."""
        codigo = codigo.strip()
        if "code=" in codigo:      # colou a URL inteira (parse_qs já decodifica)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(codigo).query)
            codigo = q.get("code", [""])[0]
        elif "%" in codigo:        # código ainda codificado
            codigo = urllib.parse.unquote(codigo)
        t = _checar(_req(f"{API}/oauth/token/", {
            "client_key": CLIENT_KEY, "client_secret": CLIENT_SECRET, "code": codigo,
            "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI}, form=True))
        self._guardar_tokens(t)
        if "video.upload" not in t.get("scope", ""):
            raise ErroTikTok("scope_not_authorized",
                             "a autorização não incluiu o envio de vídeos (video.upload)")
        try:
            info = _checar(_req(f"{API}/user/info/?fields=open_id,display_name",
                                cabecalhos={"Authorization": f"Bearer {self.token()}"}))
            d = self._ler()
            d["nome"] = (info.get("user") or {}).get("display_name")
            self._salvar(d)
        except Exception:
            pass
        return self.nome()

    def token(self):
        d = self._ler()
        if not d.get("refresh_token"):
            raise ErroTikTok("nao_conectado", "TikTok não conectado — mande /tiktok no grupo")
        if d.get("expira", 0) < time.time():
            t = _checar(_req(f"{API}/oauth/token/", {
                "client_key": CLIENT_KEY, "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token", "refresh_token": d["refresh_token"]}, form=True))
            self._guardar_tokens(t)
            d = self._ler()
        return d["access_token"]

    def desconectar(self):
        self._salvar({})

    # -------------------------------------------------------------- envio
    def enviar_rascunho(self, caminho):
        """Manda o vídeo pra caixa de entrada (rascunho) do TikTok. Retorna o publish_id."""
        tamanho = Path(caminho).stat().st_size
        if tamanho > 64 * 1024 * 1024:
            raise ErroTikTok("arquivo_grande", "vídeo maior que 64 MB")
        auth = {"Authorization": f"Bearer {self.token()}"}
        dados = _checar(_req(f"{API}/post/publish/inbox/video/init/", {
            "source_info": {"source": "FILE_UPLOAD", "video_size": tamanho,
                            "chunk_size": tamanho, "total_chunk_count": 1}}, auth))
        req = urllib.request.Request(dados["upload_url"], data=Path(caminho).read_bytes(), method="PUT",
                                     headers={"Content-Type": "video/mp4", "Content-Length": str(tamanho),
                                              "Content-Range": f"bytes 0-{tamanho - 1}/{tamanho}"})
        try:
            with urllib.request.urlopen(req, timeout=600):
                pass
        except urllib.error.HTTPError as ex:
            raise ErroTikTok(f"upload_{ex.code}", ex.reason)
        return dados["publish_id"]

    def status(self, publish_id):
        """PROCESSING_UPLOAD · SEND_TO_USER_INBOX (chegou no rascunho) · PUBLISH_COMPLETE · FAILED"""
        d = _checar(_req(f"{API}/post/publish/status/fetch/", {"publish_id": publish_id},
                         {"Authorization": f"Bearer {self.token()}"}))
        return d.get("status"), d.get("fail_reason")


ERROS_AMIGAVEIS = {
    "spam_risk_too_many_pending_share": "o TikTok já tem 5 rascunhos esperando. Poste ou apague alguns no app "
                                        "e toque em 📲 de novo.",
    "spam_risk_user_banned_from_posting": "o TikTok bloqueou temporariamente envios pra essa conta.",
    "scope_not_authorized": "a conta não autorizou o envio de vídeos. Mande /tiktok e autorize de novo.",
    "access_token_invalid": "a conexão com o TikTok expirou. Mande /tiktok e conecte de novo.",
    "nao_conectado": "o TikTok ainda não está conectado. Mande /tiktok no grupo.",
    "rate_limit_exceeded": "muitos envios seguidos. Tente de novo em 1 minuto.",
}


def erro_amigavel(ex):
    codigo = getattr(ex, "codigo", None)
    return ERROS_AMIGAVEIS.get(codigo, str(ex))
