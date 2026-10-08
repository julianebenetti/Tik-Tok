#!/usr/bin/env python3
"""
Central TikTok — robô do Telegram que organiza os vídeos UGC da Juliane.

Grupo do Telegram com tópicos:
  📥 Brutos         você encaminha os vídeos crus (+ nome do arquivo logo abaixo)
  ✂️ Editados       o robô corta as paradas, escreve a headline na tela e pede sua
                    aprovação (cortes e headline)
  ⏰ Hora de postar agenda dos aprovados (6/dia, 17h–22h, horários quebrados): vídeos de
                    hoje e dos próximos dias, separados por dia e em ordem.
                    No horário, avisa. Você posta e toca em Postei.
  🚀 Postados       histórico

Independente da AfiliDash. Só usa a biblioteca padrão do Python + ffmpeg.

Variáveis de ambiente (no VPS ficam em /etc/central-tiktok.env):
  TELEGRAM_BOT_TOKEN, USUARIOS_PERMITIDOS (IDs separados por vírgula),
  CENTRAL_DADOS (pasta de dados), CENTRAL_FUSO (padrão America/Sao_Paulo)
"""
import html
import json
import os
import queue
import random
import shutil
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cortar_parados  # noqa: E402
import glossario  # noqa: E402
import texto_tela  # noqa: E402
import tiktok_api  # noqa: E402

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PERMITIDOS = {int(x) for x in os.environ.get("USUARIOS_PERMITIDOS", "").replace(" ", "").split(",") if x}
DADOS = Path(os.environ.get("CENTRAL_DADOS", Path.home() / ".central-tiktok"))
FUSO = ZoneInfo(os.environ.get("CENTRAL_FUSO", "America/Sao_Paulo"))
API = f"https://api.telegram.org/bot{TOKEN}"
ARQ_API = f"https://api.telegram.org/file/bot{TOKEN}"
LIMITE_DOWNLOAD = 20 * 1024 * 1024

POSTS_POR_DIA = 6
JANELA = (17 * 60, 22 * 60)          # 17h às 22h, em minutos
ESPERA_NOME = 30                      # segundos esperando a mensagem com o nome do arquivo
MAX_INTERVALO = 7                     # no máximo 1 semana entre vídeos do mesmo produto
DIAS_GUARDAR = 3                      # apaga arquivos de postados/descartados depois disso
DIAS_NA_AGENDA = 3                    # hoje + 2 dias aparecem como vídeos em ⏰ Hora de postar
TOPICOS = [("brutos", "📥 Brutos"), ("editados", "✂️ Editados"),
           ("hora", "⏰ Hora de postar"), ("postados", "🚀 Postados")]
DIAS_SEMANA_LONGO = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

fila = queue.Queue()
legendas_album = {}                   # media_group_id -> legenda do álbum
nomes_soltos = {}                     # chat_id -> [(msg_id, produto, sequencia, hora)] esperando o vídeo
VIZINHANCA = 3                        # nome e vídeo são "vizinhos" se estão a até 3 mensagens de distância
nome_ativo = {}                       # chat_id -> (produto, preço, msg_id, hora): nome mandado ANTES de uma
                                      # leva de vídeos sem nome (ex.: encaminhados pela aba Mídias)
VALIDADE_NOME_ATIVO = 30 * 60
textos_brutos = {}                    # chat_id -> ids das últimas mensagens de texto em Brutos (separam levas)


# =================================================================== banco
class Banco:
    def __init__(self, caminho):
        self.con = sqlite3.connect(caminho, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.trava = threading.Lock()
        self.exec("""CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT)""")
        self.exec("""CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, chat_id INTEGER,
            bruto_msg INTEGER, file_id TEXT, album TEXT, pergunta_msg INTEGER,
            produto TEXT, preco REAL, headline TEXT, angulo TEXT, usadas TEXT DEFAULT '[]',
            hashtags TEXT, chamada TEXT, dur_orig REAL, dur_final REAL, cortes INTEGER,
            base TEXT, final TEXT, editado_msg INTEGER, slot TEXT, hora_msg INTEGER,
            criado REAL, atualizado REAL)""")
        for col in ("chave TEXT", "sequencia TEXT", "nome_msg INTEGER",   # colunas novas
                    "tg_video TEXT", "aviso_msg INTEGER", "tiktok_id TEXT", "tiktok_status TEXT",
                    "tiktok_msg INTEGER"):
            try:
                self.exec(f"ALTER TABLE videos ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass

    def exec(self, sql, params=()):
        with self.trava:
            cur = self.con.execute(sql, params)
            self.con.commit()
            return cur.fetchall()

    def get(self, chave, padrao=None):
        r = self.exec("SELECT v FROM config WHERE k=?", (chave,))
        return json.loads(r[0]["v"]) if r else padrao

    def set(self, chave, valor):
        self.exec("INSERT OR REPLACE INTO config VALUES (?,?)", (chave, json.dumps(valor)))

    def video(self, vid):
        r = self.exec("SELECT * FROM videos WHERE id=?", (vid,))
        return r[0] if r else None

    def atualizar(self, vid, **campos):
        campos["atualizado"] = time.time()
        cols = ", ".join(f"{k}=?" for k in campos)
        self.exec(f"UPDATE videos SET {cols} WHERE id=?", (*campos.values(), vid))


db = None
conta_tiktok = None


# =================================================================== Telegram
def _chamar(metodo, montar, timeout):
    """Faz a chamada; se o Telegram pedir pra esperar (429, limite de mensagens), espera e tenta de novo."""
    for tentativa in range(4):
        try:
            with urllib.request.urlopen(montar(), timeout=timeout) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as ex:
            resp = json.load(ex)
        if resp.get("ok"):
            return resp["result"]
        espera = (resp.get("parameters") or {}).get("retry_after")
        if resp.get("error_code") == 429 and espera and tentativa < 3:
            time.sleep(espera + 1)
            continue
        raise RuntimeError(f"{metodo}: {resp.get('description', resp)}")


def tg(metodo, **params):
    dados = json.dumps(params).encode()
    return _chamar(metodo, lambda: urllib.request.Request(
        f"{API}/{metodo}", data=dados, headers={"Content-Type": "application/json"}), 70)


def tg_seguro(metodo, **params):
    try:
        return tg(metodo, **params)
    except Exception as e:
        print(f"aviso {metodo}: {e}")


def tg_arquivo(metodo, campo, caminho, **params):
    """Envia arquivo (multipart feito à mão)."""
    fronteira = uuid.uuid4().hex
    corpo = bytearray()
    for k, v in params.items():
        if v is None:
            continue
        v = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        corpo += (f"--{fronteira}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                  f"{v}\r\n").encode()
    corpo += (f"--{fronteira}\r\nContent-Disposition: form-data; name=\"{campo}\"; "
              f"filename=\"{Path(caminho).name}\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
    corpo += Path(caminho).read_bytes() + f"\r\n--{fronteira}--\r\n".encode()
    dados = bytes(corpo)
    return _chamar(metodo, lambda: urllib.request.Request(
        f"{API}/{metodo}", data=dados,
        headers={"Content-Type": f"multipart/form-data; boundary={fronteira}"}), 300)


def baixar(file_id, destino):
    info = tg("getFile", file_id=file_id)
    with urllib.request.urlopen(f"{ARQ_API}/{info['file_path']}", timeout=300) as r, \
            open(destino, "wb") as f:
        shutil.copyfileobj(r, f)


def grupo():
    return db.get("grupo")


def topico(nome):
    return (db.get("topicos") or {}).get(nome)


def texto(chat_id, msg, topico_nome=None, responder=None, **extra):
    params = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML", **extra}
    if topico_nome and topico(topico_nome):
        params["message_thread_id"] = topico(topico_nome)
    if responder:
        params["reply_parameters"] = {"message_id": responder, "allow_sending_without_reply": True}
    return tg_seguro("sendMessage", **params)


def botoes(*linhas):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in linha] for linha in linhas]}


# =================================================================== agenda
def agora():
    return datetime.now(FUSO).replace(tzinfo=None)


def slots_do_dia(dia):
    """6 horários 'quebrados' entre 17h e 22h — sempre os mesmos pra cada data."""
    rnd = random.Random(f"slots-{dia.isoformat()}")
    ini, fim = JANELA
    largura = (fim - ini) // POSTS_POR_DIA
    res = []
    for i in range(POSTS_POR_DIA):
        while True:
            m = ini + i * largura + rnd.randint(3, largura - 3)
            if m % 5:            # nada de 17:00, 17:15, 17:30...
                break
        res.append(datetime.combine(dia, datetime.min.time()) + timedelta(minutes=m))
    return res


def intervalo_produto(chave):
    """Dias mínimos entre dois vídeos do mesmo produto: espalha os vídeos pelo mês.
    11 vídeos → a cada 2 dias · 4 vídeos → a cada 7 · 40 vídeos → todo dia (nunca 2 no mesmo dia)."""
    n = db.exec("SELECT COUNT(*) AS n FROM videos WHERE chave=? AND status NOT IN ('descartado','erro')",
                (chave,))[0]["n"]
    return max(1, min(MAX_INTERVALO, 30 // max(n, 1)))


def proximo_slot(vid):
    v = db.video(vid)
    ocupados = {r["slot"] for r in db.exec(
        "SELECT slot FROM videos WHERE slot IS NOT NULL AND status IN ('agendado','na_hora','postado') "
        "AND id != ?", (vid,))}
    dias_produto = [datetime.strptime(r["slot"][:10], "%Y-%m-%d").date() for r in db.exec(
        "SELECT slot FROM videos WHERE chave=? AND id != ? AND slot IS NOT NULL "
        "AND status IN ('agendado','na_hora','postado')", (v["chave"], vid))] if v["chave"] else []
    gap = intervalo_produto(v["chave"]) if v["chave"] else 1
    minimo = agora() + timedelta(minutes=5)
    dia = minimo.date()
    for _ in range(400):
        if all(abs((dia - d).days) >= gap for d in dias_produto):
            for s in slots_do_dia(dia):
                chave = s.strftime("%Y-%m-%d %H:%M")
                if s >= minimo and chave not in ocupados:
                    return chave
        dia += timedelta(days=1)
    raise RuntimeError("agenda cheia")


def slot_bonito(slot):
    d = datetime.strptime(slot, "%Y-%m-%d %H:%M")
    hoje = agora().date()
    if d.date() == hoje:
        dia = "hoje"
    elif d.date() == hoje + timedelta(days=1):
        dia = "amanhã"
    else:
        dia = f"{DIAS_SEMANA[d.weekday()]} {d:%d/%m}"
    return f"{dia} às {d:%H:%M}"


# =================================================================== textos
def e(s):
    return html.escape(str(s or ""))


def preco_txt(p):
    return ("R$ " + f"{p:.2f}".replace(".", ",")) if p else "sem preço"


def dia_bonito(dia):
    hoje = agora().date()
    rel = " (hoje)" if dia == hoje else " (amanhã)" if dia == hoje + timedelta(days=1) else ""
    return f"{DIAS_SEMANA_LONGO[dia.weekday()]}, {dia:%d/%m}{rel}"


def legenda_hora(v):
    hora = datetime.strptime(v["slot"], "%Y-%m-%d %H:%M")
    topo = (f"⏰ <b>É AGORA! {hora:%H:%M}</b>" if v["status"] == "na_hora"
            else f"📅 <b>{DIAS_SEMANA[hora.weekday()]} {hora:%d/%m} · {hora:%H:%M}</b>")
    corte = (f"✂️ {v['cortes']} corte(s) · {v['dur_orig']:.1f}s → {v['dur_final']:.1f}s" if v["cortes"]
             else "✂️ sem paradas pra cortar")
    seq = f"🎬 {e(v['sequencia'])} · " if v["sequencia"] else ""
    return (f"{topo}\n"
            f"🛍 <b>{e(v['produto'])}</b>\n"
            f"📝 <b>Na tela:</b> {e(v['headline'])}\n\n"
            + (f"1️⃣ Abra o <b>rascunho no app do TikTok</b> (notificação na caixa de entrada)\n"
               if v["tiktok_status"] in ("enviado", "no_rascunho") else
               f"1️⃣ Salve o vídeo e poste no TikTok\n") +
            f"2️⃣ Legenda — botão <b>📋 Copiar legenda</b>:\n<code>{e(v['hashtags'])}</code>\n"
            f"3️⃣ Produto no TikTok Shop: <b>{e(v['produto'])}</b>\n"
            f"4️⃣ Chamada do link — botão <b>📋 Copiar chamada</b>:\n<code>{e(v['chamada'])}</code>\n\n"
            f"Postou? Toque em <b>✅ Postei</b>.\n"
            f"💬 <i>Outra headline: responda este vídeo com o texto ou toque em 🔄.</i>\n"
            f"<i>{seq}{corte}</i>")


def botoes_hora(vid):
    v = db.video(vid)
    teclado = botoes([("✅ Postei", f"po:{vid}"), ("🔄 Outra headline", f"ou:{vid}")],
                     [("🔁 Reagendar", f"re:{vid}"), ("❌ Descartar", f"de:{vid}")])
    if conta_tiktok and conta_tiktok.conectada():
        rotulo = ("📲 Mandar de novo pro TikTok" if v["tiktok_status"] in ("enviado", "no_rascunho")
                  else "📲 Mandar pro rascunho do TikTok agora")
        teclado["inline_keyboard"].insert(0, [{"text": rotulo, "callback_data": f"tk:{vid}"}])
    # botões que copiam o texto com um toque (limite do Telegram: 256 caracteres)
    copiar = [{"text": rotulo, "copy_text": {"text": txt[:256]}}
              for rotulo, txt in (("📋 Copiar legenda", v["hashtags"]), ("📋 Copiar chamada", v["chamada"])) if txt]
    if copiar:
        teclado["inline_keyboard"].insert(0, copiar)
    return teclado


def legenda_editado(v):
    corte = (f"✂️ {v['cortes']} corte(s) · {v['dur_orig']:.1f}s → {v['dur_final']:.1f}s" if v["cortes"]
             else "✂️ sem paradas pra cortar")
    seq = f" · 🎬 {e(v['sequencia'])}" if v["sequencia"] else ""
    return (f"🛍 <b>{e(v['produto'])}</b> · {preco_txt(v['preco'])}{seq}\n\n"
            f"📝 <b>Na tela:</b> {e(v['headline'])}\n\n"
            f"#️⃣ <code>{e(v['hashtags'])}</code>\n"
            f"🔗 <code>{e(v['chamada'])}</code>\n\n"
            f"{corte}\n"
            f"👀 <i>Confira os cortes e a headline. Aprovou, ele vai pra ⏰ Hora de postar.</i>\n"
            f"💬 <i>Pra usar outra headline sua, responda este vídeo com o texto.</i>")


def botoes_aprovacao(vid):
    v = db.video(vid)
    linhas = [[("✅ Aprovar", f"ap:{vid}"), ("🔄 Outra headline", f"ou:{vid}")]]
    n = db.exec("SELECT COUNT(*) AS n FROM videos WHERE chave=? AND status IN ('aguardando','fila','processando')",
                (v["chave"],))[0]["n"] if v and v["chave"] else 0
    if n > 1:
        linhas.append([("✅✅ Aprovar todos deste produto", f"at:{vid}")])
    linhas.append([("❌ Descartar", f"de:{vid}")])
    return botoes(*linhas)


def agendar(vid):
    """Aprovado: ganha horário e sai de ✂️ Editados (a agenda em ⏰ Hora de postar é atualizada depois)."""
    slot = proximo_slot(vid)
    db.atualizar(vid, status="agendado", slot=slot)
    v = db.video(vid)
    if v["editado_msg"]:
        apagar_msgs(v["chat_id"], v["editado_msg"])
        db.atualizar(vid, editado_msg=None)
    return slot


# =================================================================== processamento
def pasta_video(vid):
    p = DADOS / "videos" / str(vid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def nova_headline(v, info, evitar=None):
    usadas = json.loads(v["usadas"] or "[]")
    # evita repetir headline já usada em outro vídeo do mesmo produto
    do_produto = set(usadas)
    for r in db.exec("SELECT usadas FROM videos WHERE chave=? AND id != ?", (v["chave"], v["id"])):
        do_produto.update(json.loads(r["usadas"] or "[]"))
    idx, ang, txt = glossario.escolher_headline(info, v["preco"], evitar_angulo=evitar, ja_usadas=do_produto)
    return idx, ang, txt, json.dumps(usadas + [idx])


def trocar_video(v, msg_id, legenda, teclado):
    """Troca o vídeo de uma mensagem sem mudar ela de lugar. Retorna False se não deu."""
    try:
        res = tg_arquivo("editMessageMedia", "video", v["final"], chat_id=v["chat_id"], message_id=msg_id,
                         media={"type": "video", "media": "attach://video", "caption": legenda,
                                "parse_mode": "HTML", "supports_streaming": True},
                         reply_markup=teclado)
        db.atualizar(v["id"], tg_video=(res.get("video") or {}).get("file_id"))
        return True
    except Exception as ex:
        print("editMessageMedia:", ex)
        return False


def enviar_editado(vid):
    v = db.video(vid)
    res = tg_arquivo("sendVideo", "video", v["final"], chat_id=v["chat_id"],
                     message_thread_id=topico("editados"), caption=legenda_editado(v),
                     parse_mode="HTML", supports_streaming="true", reply_markup=botoes_aprovacao(vid))
    antigo = v["editado_msg"]
    db.atualizar(vid, editado_msg=res["message_id"], status="aguardando",
                 tg_video=(res.get("video") or {}).get("file_id"))
    apagar_msgs(v["chat_id"], antigo)


def processar(vid):
    v = db.video(vid)
    pasta = pasta_video(vid)
    db.atualizar(vid, status="processando")
    bruto = pasta / "bruto.mp4"
    baixar(v["file_id"], bruto)

    args = Namespace(limiar=db.get("limiar", 1.0), min_parado=db.get("min_parado", 0.5),
                     folga=0.1, min_final=3.0, crf=18, analisar=False)
    r = cortar_parados.processar(bruto, pasta, args)
    if r["status"].startswith("pulado"):
        db.atualizar(vid, status="erro")
        motivo = ("está parado do começo ao fim" if "todo" in r["status"]
                  else f"ficaria com só {r['duracao_final']:.1f}s depois dos cortes (tente /limiar menor)")
        texto(v["chat_id"], f"⚠️ Não editei este vídeo: ele {motivo}.", "brutos", v["bruto_msg"])
        return
    base = Path(r["saida"]) if r["saida"] else bruto

    info = glossario.identificar(v["produto"])
    idx, ang, headline, usadas = nova_headline(v, info, evitar=db.get("ultimo_angulo"))
    db.set("ultimo_angulo", ang)
    final = pasta / "final.mp4"
    texto_tela.aplicar_headline(base, final, headline)
    db.atualizar(vid, headline=headline, angulo=ang, usadas=usadas,
                 hashtags=glossario.hashtags(info), chamada=glossario.chamada(info),
                 dur_orig=r["duracao_original"], dur_final=r["duracao_final"], cortes=r["cortes"],
                 base=str(base), final=str(final), tg_video=None)
    enviar_editado(vid)
    if base != bruto:
        bruto.unlink(missing_ok=True)
    tg_seguro("setMessageReaction", chat_id=v["chat_id"], message_id=v["bruto_msg"],
              reaction=[{"type": "emoji", "emoji": "👍"}])


def refazer_headline(vid, headline_propria=None):
    v = db.video(vid)
    info = glossario.identificar(v["produto"])
    if headline_propria:
        headline, ang, usadas = headline_propria, "propria", v["usadas"]
    else:
        _, ang, headline, usadas = nova_headline(v, info, evitar=v["angulo"])
    final = Path(v["final"])
    novo = final.with_name("final_novo.mp4")
    texto_tela.aplicar_headline(v["base"], novo, headline)
    novo.replace(final)
    db.atualizar(vid, headline=headline, angulo=ang, usadas=usadas, tg_video=None)
    v = db.video(vid)   # relê: o status pode ter mudado enquanto o vídeo era reescrito
    if v["status"] == "aguardando":
        if not trocar_video(v, v["editado_msg"], legenda_editado(v), botoes_aprovacao(vid)):
            enviar_editado(vid)
    elif v["status"] in ("agendado", "na_hora") and v["hora_msg"]:
        # troca o vídeo na mesma mensagem (não muda a ordem da agenda); se não der, republica
        if not trocar_video(v, v["hora_msg"], legenda_hora(v), botoes_hora(vid)):
            db.atualizar(vid, hora_msg=None)
            sincronizar_agenda(forcar=True)


def trabalhador():
    while True:
        tarefa, vid, extra = fila.get()
        try:
            if tarefa == "processar":
                processar(vid)
            elif tarefa == "headline":
                refazer_headline(vid, extra)
            elif tarefa == "tiktok":
                mandar_pro_tiktok(vid)
        except Exception as ex:
            traceback.print_exc()
            v = db.video(vid)
            if tarefa == "processar":
                db.atualizar(vid, status="erro")
            if v:
                if tarefa == "processar":
                    onde, msg_id = "brutos", v["bruto_msg"]
                elif v["status"] == "aguardando":
                    onde, msg_id = "editados", v["editado_msg"]
                else:
                    onde, msg_id = "hora", v["hora_msg"]
                texto(v["chat_id"], f"❌ Deu erro neste vídeo: {e(str(ex)[:300])}", onde, msg_id)
        finally:
            fila.task_done()


# =================================================================== agendador
trava_agenda = threading.RLock()


def publicar_video(v):
    """Manda o vídeo pra ⏰ Hora de postar (reaproveita o arquivo já enviado, sem novo upload)."""
    params = dict(chat_id=v["chat_id"], message_thread_id=topico("hora"), caption=legenda_hora(v),
                  parse_mode="HTML", supports_streaming=True, reply_markup=botoes_hora(v["id"]))
    res = None
    if v["tg_video"]:
        try:
            res = tg("sendVideo", video=v["tg_video"], **params)
        except Exception as ex:
            print("sendVideo por file_id:", ex)
    if res is None:
        res = tg_arquivo("sendVideo", "video", v["final"], **params)
    db.atualizar(v["id"], hora_msg=res["message_id"], tg_video=(res.get("video") or {}).get("file_id"))


def apagar_msgs(chat, *ids):
    for i in ids:
        if i:
            tg_seguro("deleteMessage", chat_id=chat, message_id=i)


def eh_subsequencia(curta, longa):
    it = iter(longa)
    return all(x in it for x in curta)


def cabecalho_dia(d, n):
    dia = datetime.strptime(d, "%Y-%m-%d").date()
    return f"📆 <b>{dia_bonito(dia)}</b> — {n} vídeo{'s' if n > 1 else ''}"


def sincronizar_agenda(forcar=False):
    """Deixa ⏰ Hora de postar igual à agenda: um cabeçalho por dia e os vídeos em ordem de horário,
    de hoje até DIAS_NA_AGENDA dias. Só reenvia a partir do primeiro dia que mudou de ordem
    (vídeo novo entrando no meio); postar/descartar só apaga a mensagem, sem reenviar nada."""
    chat = grupo()
    if not chat or not topico("hora"):
        return
    with trava_agenda:
        pub = db.get("agenda_pub", {})        # {data: {"cab": msg_id, "ids": [ids em ordem]}}
        hoje = agora().date()
        janela = [(hoje + timedelta(days=i)).isoformat() for i in range(DIAS_NA_AGENDA)]
        ativos = db.exec("SELECT * FROM videos WHERE status IN ('agendado','na_hora') AND slot IS NOT NULL "
                         "ORDER BY slot")
        desejado = {d: [v["id"] for v in ativos if v["slot"][:10] == d] for d in janela}
        # dias que já passaram: só tira o que foi postado/descartado
        for d in [d for d in pub if d < janela[0]]:
            pub[d]["ids"] = [i for i in pub[d]["ids"] if any(v["id"] == i for v in ativos)]
            if not pub[d]["ids"]:
                apagar_msgs(chat, pub.pop(d).get("cab"))
        # primeiro dia da janela que precisa ser reenviado
        inicio = None
        for d in janela:
            atual = pub.get(d, {}).get("ids", [])
            if forcar or not eh_subsequencia(desejado[d], atual) or (desejado[d] and d not in pub):
                inicio = d
                break
            if atual != desejado[d]:          # só saíram vídeos: atualiza a lista, sem reenviar
                if desejado[d]:
                    pub[d]["ids"] = desejado[d]
                else:
                    apagar_msgs(chat, pub.pop(d).get("cab"))
        if inicio:
            refazer = [d for d in janela if d >= inicio]
            # apaga o que estava publicado desses dias (e vídeos que saíram deles)
            for d in refazer:
                antigo = pub.pop(d, None)
                if antigo:
                    apagar_msgs(chat, antigo.get("cab"))
                    for i in antigo["ids"]:
                        x = db.video(i)
                        if x:
                            apagar_msgs(chat, x["hora_msg"], x["aviso_msg"])
                            db.atualizar(i, hora_msg=None, aviso_msg=None)
            for d in refazer:
                if not desejado[d]:
                    continue
                cab = texto(chat, cabecalho_dia(d, len(desejado[d])), "hora")
                for i in desejado[d]:
                    publicar_video(db.video(i))
                pub[d] = {"cab": cab["message_id"] if cab else None, "ids": desejado[d],
                          "txt": cabecalho_dia(d, len(desejado[d]))}
        # cabeçalhos com contagem ou "(hoje)/(amanhã)" desatualizados: edita no lugar
        for d, info in pub.items():
            novo = cabecalho_dia(d, len(info["ids"]))
            if info.get("cab") and info.get("txt") != novo:
                tg_seguro("editMessageText", chat_id=chat, message_id=info["cab"], text=novo, parse_mode="HTML")
                info["txt"] = novo
        # vídeo que saiu da janela (reagendado pra longe) mas ainda tem mensagem: apaga
        na_janela = {i for d in pub for i in pub[d]["ids"]}
        for v in db.exec("SELECT * FROM videos WHERE hora_msg IS NOT NULL AND status IN ('agendado','na_hora')"):
            if v["id"] not in na_janela:
                apagar_msgs(chat, v["hora_msg"], v["aviso_msg"])
                db.atualizar(v["id"], hora_msg=None, aviso_msg=None)
        db.set("agenda_pub", pub)
        atualizar_fila()


def atualizar_fila():
    """Mensagem fixada no topo de ⏰ Hora de postar: resumo da agenda, inclusive dos dias mais distantes."""
    chat = grupo()
    if not chat or not topico("hora"):
        return
    with trava_agenda:
        limite = (agora().date() + timedelta(days=DIAS_NA_AGENDA)).isoformat()
        depois = db.exec("SELECT * FROM videos WHERE status='agendado' AND slot >= ? ORDER BY slot", (limite,))
        total = db.exec("SELECT COUNT(*) AS n FROM videos WHERE status IN ('agendado','na_hora')")[0]["n"]
        editando = db.exec("SELECT COUNT(*) AS n FROM videos WHERE status='aguardando'")[0]["n"]
        por_dia = {}
        for v in depois:
            por_dia.setdefault(v["slot"][:10], []).append(v)
        linhas = []
        for d, vs in list(por_dia.items())[:10]:
            dia = datetime.strptime(d, "%Y-%m-%d").date()
            linhas.append(f"• {DIAS_SEMANA[dia.weekday()]} {dia:%d/%m}: " + ", ".join(e(v["produto"]) for v in vs))
        if len(por_dia) > 10:
            linhas.append(f"… e mais {len(por_dia) - 10} dias (até {depois[-1]['slot'][8:10]}/{depois[-1]['slot'][5:7]})")
        txt = (f"📅 <b>Agenda</b> — {total} vídeo(s) agendado(s)"
               + (f" · ⏳ {editando} esperando aprovação em ✂️ Editados" if editando else "") + "\n\n"
               f"Aqui embaixo: os vídeos de hoje e dos próximos {DIAS_NA_AGENDA - 1} dias, em ordem.\n"
               + ("\n<b>Depois disso:</b>\n" + "\n".join(linhas) if linhas else "")
               + ("\n\n🚀 Quer adiantar um desses? Toque nele abaixo." if depois else ""))
        teclado = {"inline_keyboard": [
            [{"text": f"🚀 {datetime.strptime(v['slot'], '%Y-%m-%d %H:%M'):%d/%m} · {v['produto'][:30]}",
              "callback_data": f"pa:{v['id']}"}] for v in depois[:5]]}
        msg_id = db.get("fila_msg")
        if msg_id:
            try:
                tg("editMessageText", chat_id=chat, message_id=msg_id, text=txt, parse_mode="HTML",
                   reply_markup=teclado)
                return
            except Exception as ex:
                if "not modified" in str(ex):
                    return
        m = texto(chat, txt, "hora", reply_markup=teclado)
        if m:
            db.set("fila_msg", m["message_id"])
            tg_seguro("pinChatMessage", chat_id=chat, message_id=m["message_id"], disable_notification=True)


def mandar_pro_tiktok(vid):
    """Manda o vídeo final pra caixa de entrada (rascunho) do TikTok e avisa no Telegram."""
    v = db.video(vid)
    if not v or v["status"] not in ("agendado", "na_hora"):
        return
    try:
        publish_id = conta_tiktok.enviar_rascunho(v["final"])
    except Exception as ex:
        db.atualizar(vid, tiktok_status="erro")
        texto(v["chat_id"], f"⚠️ Não consegui mandar pro TikTok: {e(tiktok_api.erro_amigavel(ex))}\n"
                            "Dá pra postar salvando o vídeo daqui mesmo.", "hora", v["hora_msg"])
        return
    db.atualizar(vid, tiktok_id=publish_id, tiktok_status="enviado")
    v = db.video(vid)
    if v["hora_msg"]:
        tg_seguro("editMessageCaption", chat_id=v["chat_id"], message_id=v["hora_msg"],
                  caption=legenda_hora(v), parse_mode="HTML", reply_markup=botoes_hora(vid))
    m = texto(v["chat_id"], f"📲 <b>Mandei pro rascunho do seu TikTok!</b> {e(v['produto'])}\n"
                            "Abra o app → notificação na caixa de entrada → confira, escolha o produto do "
                            "TikTok Shop, cole a legenda e publique. Depois toque em ✅ Postei.",
              "hora", v["hora_msg"])
    if m:
        db.atualizar(vid, tiktok_msg=m["message_id"])


def acompanhar_tiktok():
    """Confere se os rascunhos enviados chegaram (ou falharam) no TikTok."""
    for v in db.exec("SELECT * FROM videos WHERE tiktok_status='enviado' AND tiktok_id IS NOT NULL "
                     "AND status IN ('agendado','na_hora')"):
        try:
            st, motivo = conta_tiktok.status(v["tiktok_id"])
        except Exception as ex:
            print("status tiktok:", ex)
            continue
        if st in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            db.atualizar(v["id"], tiktok_status="no_rascunho")
        elif st == "FAILED":
            db.atualizar(v["id"], tiktok_status="erro")
            texto(v["chat_id"], f"⚠️ O TikTok recusou o rascunho de {e(v['produto'])}: {e(motivo)}. "
                                "Toque em 📲 pra tentar de novo ou poste salvando o vídeo.", "hora", v["hora_msg"])


def chegou_a_hora(v):
    """No horário: marca o vídeo e avisa (notificação) respondendo a ele na agenda."""
    if not v["hora_msg"]:
        sincronizar_agenda()
        v = db.video(v["id"])
    db.atualizar(v["id"], status="na_hora")
    v = db.video(v["id"])
    if v["hora_msg"]:
        tg_seguro("editMessageCaption", chat_id=v["chat_id"], message_id=v["hora_msg"],
                  caption=legenda_hora(v), parse_mode="HTML", reply_markup=botoes_hora(v["id"]))
    aviso = texto(v["chat_id"], f"⏰ <b>Hora de postar!</b> {e(v['produto'])} 👆", "hora", v["hora_msg"])
    if aviso:
        db.atualizar(v["id"], aviso_msg=aviso["message_id"])
    if conta_tiktok and conta_tiktok.conectada() and v["tiktok_status"] not in ("enviado", "no_rascunho"):
        fila.put(("tiktok", v["id"], None))


def atualizar_glossario():
    try:
        fonte, n = glossario.carregar(cache=DADOS / "glossario.json")
        print(f"glossário: {n} headlines ({fonte})")
        return fonte, n
    except Exception as ex:
        print("glossário:", ex)
        return None, len(glossario.HEADLINES)


def agendador():
    ultima_limpeza, ultimo_glossario = 0, time.time()
    while True:
        try:
            # 0) glossário novo do GitHub a cada 6h (a rotina sincroniza com a página às 5h52)
            if time.time() - ultimo_glossario > 6 * 3600:
                ultimo_glossario = time.time()
                atualizar_glossario()
            # 1) vídeos sem produto: pega a legenda do álbum ou pergunta
            sem_nome = {}
            for v in db.exec("SELECT * FROM videos WHERE status='sem_produto' AND pergunta_msg IS NULL "
                             "AND criado < ? ORDER BY bruto_msg", (time.time() - ESPERA_NOME,)):
                leg = legendas_album.get(v["album"]) if v["album"] else None
                ativo = nome_ativo.get(v["chat_id"])
                if leg:
                    definir_produto(v["id"], *nome_do_texto(leg))
                elif ativo and ativo[2] < v["bruto_msg"] and time.time() - ativo[3] < VALIDADE_NOME_ATIVO:
                    definir_produto(v["id"], ativo[0], ativo[1], None)  # nome mandado antes da leva
                else:
                    sem_nome.setdefault(v["chat_id"], []).append(v)
            for chat, vs in sem_nome.items():   # UMA pergunta pra todos os vídeos sem nome
                n = len(vs)
                m = texto(chat, (f"🛍 <b>{n} vídeo{'s' if n > 1 else ''} sem o nome do produto.</b>\n"
                                 f"<b>Responda esta mensagem</b> com o nome — vale pra "
                                 f"{'todos eles' if n > 1 else 'ele'}:\n"
                                 "<code>nome do produto no TikTok | preço</code> "
                                 "(ou o nome do arquivo, ex.: VESTIDO_LONGO_G1C2A1.mp4)\n\n"
                                 "💡 <i>Dica: se for encaminhar vários vídeos do mesmo produto sem a mensagem de "
                                 "nome (pela aba Mídias), mande o nome antes e depois os vídeos.</i>"),
                          "brutos", vs[0]["bruto_msg"])
                for v in vs:
                    db.atualizar(v["id"], pergunta_msg=m["message_id"] if m else 0)
            # 2) virada do dia: o próximo dia entra na agenda
            if db.get("agenda_dia") != agora().date().isoformat():
                db.set("agenda_dia", agora().date().isoformat())
                sincronizar_agenda()
            # 3) horários que chegaram
            for v in db.exec("SELECT * FROM videos WHERE status='agendado' AND slot <= ? ORDER BY slot",
                             (agora().strftime("%Y-%m-%d %H:%M"),)):
                try:
                    chegou_a_hora(v)
                except Exception:
                    traceback.print_exc()
            # 4) rascunhos enviados: chegaram no TikTok?
            if conta_tiktok and conta_tiktok.conectada():
                acompanhar_tiktok()
            # 5) limpeza dos arquivos antigos
            if time.time() - ultima_limpeza > 3600:
                ultima_limpeza = time.time()
                # vídeo que ficou sem produto por mais de 1 dia (apagado/esquecido) sai da fila
                db.exec("UPDATE videos SET status='erro' WHERE status='sem_produto' AND criado < ?",
                        (time.time() - 86400,))
                for v in db.exec("SELECT id FROM videos WHERE status IN ('postado','descartado','erro') "
                                 "AND atualizado < ?", (time.time() - DIAS_GUARDAR * 86400,)):
                    shutil.rmtree(DADOS / "videos" / str(v["id"]), ignore_errors=True)
        except Exception:
            traceback.print_exc()
        time.sleep(15)


# =================================================================== mensagens
AJUDA = (
    "🎬 <b>Central TikTok</b>\n\n"
    "<b>1.</b> Encaminhe os vídeos crus pra <b>📥 Brutos</b>, cada um com o nome do arquivo logo "
    "abaixo (ou a legenda <code>nome do produto | preço</code>).\n\n"
    "<b>2.</b> Em <b>✂️ Editados</b> eu devolvo cortado e com a headline na tela. Confira e aprove "
    "(ou peça outra headline / responda com a sua).\n\n"
    "<b>3.</b> Aprovados entram na agenda (6 por dia, 17h–22h; o mesmo produto nunca no mesmo dia). "
    "Em <b>⏰ Hora de postar</b> ficam os vídeos de hoje e dos próximos dias, em ordem. "
    "No horário eu aviso. Postou? Toque em <b>✅ Postei</b>. Pode postar antes também.\n\n"
    "Comandos: /agenda · /tiktok (conectar o TikTok) · /glossario (recarrega headlines e hashtags) · "
    "/limiar 1.5 (corta mais) · "
    "/limiar 0.7 (corta menos) · /configurar"
)


def configurar(msg):
    chat = msg["chat"]
    if not chat.get("is_forum"):
        texto(chat["id"], "Primeiro ative os <b>Tópicos</b> no grupo (Editar grupo → Tópicos) "
                          "e me deixe como administrador com permissão de gerenciar tópicos.")
        return
    topicos = db.get("topicos") or {}
    if grupo() != chat["id"]:
        topicos = {}
    for chave, nome in TOPICOS:
        if chave not in topicos:
            try:
                topicos[chave] = tg("createForumTopic", chat_id=chat["id"], name=nome)["message_thread_id"]
            except Exception as ex:
                texto(chat["id"], f"Não consegui criar o tópico {nome}: {e(ex)}\n"
                                  "Confira se sou administrador com permissão de gerenciar tópicos.")
                return
    db.set("grupo", chat["id"])
    db.set("topicos", topicos)
    texto(chat["id"], "✅ Tudo pronto! Criei os tópicos.\n\n" + AJUDA)


def definir_produto(vid, nome, preco=None, sequencia=None, nome_msg=None):
    campos = dict(produto=nome, preco=preco, chave=glossario.chave_produto(nome), status="fila")
    if sequencia:
        campos["sequencia"] = sequencia
    if nome_msg:
        campos["nome_msg"] = nome_msg
    db.atualizar(vid, **campos)
    fila.put(("processar", vid, None))


def nome_do_texto(txt):
    """Aceita 'nome | preço' ou o nome de arquivo do gerador. → (nome, preco, sequencia)"""
    arq = glossario.ler_nome_arquivo(txt)
    if arq:
        return arq[0], None, arq[1]
    nome, preco = glossario.ler_legenda(txt)
    return nome, preco, None


def receber_video(msg, video):
    file_id, tamanho = video
    if tamanho and tamanho > LIMITE_DOWNLOAD:
        texto(msg["chat"]["id"], f"⚠️ Esse vídeo tem {tamanho / 1048576:.0f} MB e o Telegram só deixa "
                                 "robôs baixarem até 20 MB.", "brutos", msg["message_id"])
        return
    album = msg.get("media_group_id")
    leg = msg.get("caption")
    if album and leg:
        legendas_album[album] = leg
        # outros vídeos do mesmo álbum que chegaram antes da legenda
        for v in db.exec("SELECT id FROM videos WHERE status='sem_produto' AND album=? "
                         "AND pergunta_msg IS NULL", (album,)):
            definir_produto(v["id"], *nome_do_texto(leg))
    chat = msg["chat"]["id"]
    db.exec("INSERT INTO videos (status, chat_id, bruto_msg, file_id, album, criado, atualizado) "
            "VALUES ('sem_produto',?,?,?,?,?,?)", (chat, msg["message_id"], file_id, album, time.time(), time.time()))
    vid = db.exec("SELECT last_insert_rowid() AS id")[0]["id"]

    # de onde vem o nome do produto, em ordem de preferência
    nome = preco = seq = None
    if leg or legendas_album.get(album):
        nome, preco, seq = nome_do_texto(leg or legendas_album.get(album))
    if not nome:                                   # nome do arquivo anexado ao próprio vídeo
        arq = glossario.ler_nome_arquivo((msg.get("video") or msg.get("document") or {}).get("file_name"))
        if arq:
            nome, seq = arq
    if not nome:   # o nome (mensagem logo depois do vídeo) chegou antes do vídeo ser processado
        soltos = nomes_soltos.get(chat, [])
        for item in soltos:
            if 0 < item[0] - msg["message_id"] <= VIZINHANCA:
                _, nome, seq, _ = item
                soltos.remove(item)
                break
    if nome:
        definir_produto(vid, nome, preco, seq)
    # senão espera a próxima mensagem com o nome do arquivo (ou pergunta depois de ESPERA_NOME s)


def pegar_video(msg):
    if "video" in msg:
        return msg["video"]["file_id"], msg["video"].get("file_size", 0)
    doc = msg.get("document") or {}
    if doc.get("mime_type", "").startswith("video/"):
        return doc["file_id"], doc.get("file_size", 0)
    return None


def tratar_mensagem(msg):
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {}).get("id")
    txt = (msg.get("text") or "").strip()
    cmd = txt.split()[0].split("@")[0].lower() if txt.startswith("/") else None

    if user not in PERMITIDOS:
        if cmd or msg["chat"]["type"] == "private":
            texto(chat_id, f"🔒 Robô privado. Seu ID do Telegram é <code>{user}</code>.")
        return
    if msg["chat"]["type"] == "private":
        texto(chat_id, "Eu trabalho no <b>grupo</b> da Central. Me adicione num grupo com Tópicos "
                       "ativados, como administrador, e mande /configurar lá.")
        return

    if cmd == "/configurar":
        configurar(msg)
        return
    if chat_id != grupo():
        if cmd:
            texto(chat_id, "Este grupo ainda não está configurado. Mande /configurar.")
        return

    if cmd in ("/start", "/ajuda", "/help"):
        texto(chat_id, AJUDA, responder=msg["message_id"], message_thread_id=msg.get("message_thread_id"))
        return
    if cmd == "/agenda":
        ag = db.exec("SELECT * FROM videos WHERE status='agendado' ORDER BY slot")
        linhas = [f"• {slot_bonito(v['slot'])} — {e(v['produto'])}" for v in ag[:15]]
        if len(ag) > 15:
            ultimo = slot_bonito(ag[-1]["slot"])
            linhas.append(f"… e mais {len(ag) - 15} (até {ultimo})")
        aguardando = db.exec("SELECT COUNT(*) AS n FROM videos WHERE status='aguardando'")[0]["n"]
        corpo = "\n".join(linhas) or "Nada agendado ainda."
        texto(chat_id, f"📅 <b>Agenda</b>\n{corpo}\n\n⏳ Esperando sua aprovação em ✂️ Editados: {aguardando}",
              responder=msg["message_id"], message_thread_id=msg.get("message_thread_id"))
        return
    if cmd == "/tiktok":
        if not tiktok_api.configurado():
            texto(chat_id, "🔌 O app do TikTok ainda não está configurado no servidor.\n"
                           "Depois de criar o app no TikTok for Developers, rode o instalador de novo no VPS — "
                           "ele vai pedir a <b>Client key</b> e o <b>Client secret</b>.",
                  responder=msg["message_id"], message_thread_id=msg.get("message_thread_id"))
        else:
            atual = (f"✅ Conectado: <b>{e(conta_tiktok.nome() or 'sua conta')}</b>\n\n"
                     if conta_tiktok.conectada() else "")
            texto(chat_id, f"{atual}🔗 <b>Conectar o TikTok</b>\n"
                           f"1. Abra: <a href=\"{e(conta_tiktok.link_autorizacao())}\">autorizar a Central no TikTok</a>\n"
                           "2. Entre na sua conta e toque em <b>Autorizar</b>\n"
                           "3. A página vai mostrar uma mensagem <code>/tiktok_codigo …</code> — copie e mande aqui.\n\n"
                           "Pra desconectar: /tiktok_sair",
                  responder=msg["message_id"], message_thread_id=msg.get("message_thread_id"),
                  link_preview_options={"is_disabled": True})
        return
    if cmd == "/tiktok_codigo":
        partes = txt.split(maxsplit=1)
        tg_seguro("deleteMessage", chat_id=chat_id, message_id=msg["message_id"])   # o código é sensível
        try:
            nome = conta_tiktok.trocar_codigo(partes[1] if len(partes) > 1 else "")
            resp = (f"✅ <b>TikTok conectado</b>{': ' + e(nome) if nome else ''}!\n"
                    "A partir de agora, no horário de cada vídeo eu mando ele pro rascunho do seu TikTok.")
            sincronizar_agenda(forcar=True)   # os vídeos da agenda ganham o botão 📲
        except Exception as ex:
            resp = f"⚠️ Não consegui conectar: {e(tiktok_api.erro_amigavel(ex))}\nMande /tiktok e tente de novo."
        texto(chat_id, resp, message_thread_id=msg.get("message_thread_id"))
        return
    if cmd == "/tiktok_sair":
        conta_tiktok.desconectar()
        texto(chat_id, "🔌 TikTok desconectado. Pra revogar de vez: app do TikTok → Configurações e privacidade "
                       "→ Segurança → Apps e serviços.", message_thread_id=msg.get("message_thread_id"))
        sincronizar_agenda(forcar=True)
        return
    if cmd == "/glossario":
        fonte, n = atualizar_glossario()
        origem = {"github": "versão mais nova do GitHub", "cache": "última versão salva (GitHub fora do ar)",
                  "local": "versão que veio na instalação"}.get(fonte, "erro ao carregar")
        tags = sum(len(v) for v in glossario.HASHTAGS.values())
        texto(chat_id, f"📚 Glossário: {n} headlines, {tags} conjuntos de hashtags\nFonte: {origem}",
              responder=msg["message_id"], message_thread_id=msg.get("message_thread_id"))
        return
    if cmd in ("/limiar", "/minparado"):
        chave = "limiar" if cmd == "/limiar" else "min_parado"
        try:
            valor = float(txt.split()[1].replace(",", "."))
            assert 0 < valor <= 20
            db.set(chave, valor)
            resp = f"Feito: {cmd[1:]} = {valor}. Vale para os próximos vídeos."
        except Exception:
            resp = f"Use assim: {cmd} 1.5  (atual: {db.get(chave, 1.0 if chave == 'limiar' else 0.5)})"
        texto(chat_id, resp, responder=msg["message_id"], message_thread_id=msg.get("message_thread_id"))
        return

    thread = msg.get("message_thread_id")
    video = pegar_video(msg)
    resposta = (msg.get("reply_to_message") or {}).get("message_id")

    if thread == topico("brutos"):
        midia = msg.get("video") or msg.get("document") or {}
        print(f"brutos #{msg['message_id']}: {'VÍDEO' if video else 'texto'} "
              f"arquivo={midia.get('file_name')!r} legenda={(msg.get('caption') or '')[:60]!r} "
              f"texto={txt[:70]!r} album={msg.get('media_group_id')} resposta_a={resposta} "
              f"encaminhado={'forward_origin' in msg}")
        if video:
            receber_video(msg, video)
        elif txt and resposta:
            # resposta à pergunta "qual o produto?" (vale pra todos dela) ou direto a um vídeo
            r = db.exec("SELECT * FROM videos WHERE status='sem_produto' AND (pergunta_msg=? OR bruto_msg=?)",
                        (resposta, resposta))
            for x in r:
                definir_produto(x["id"], *nome_do_texto(txt), nome_msg=msg["message_id"])
            if r:
                tg_seguro("setMessageReaction", chat_id=chat_id, message_id=msg["message_id"],
                          reaction=[{"type": "emoji", "emoji": "👌"}])
        elif txt:
            # nome do arquivo logo abaixo do vídeo (ou "nome | preço") → vale pro(s) vídeo(s) sem produto acima
            nome, preco, seq = nome_do_texto(txt)
            if not nome:
                return
            textos_brutos[chat_id] = (textos_brutos.get(chat_id, []) + [msg["message_id"]])[-50:]
            mid = msg["message_id"]
            # 1º: os vídeos sem nome logo acima — um (par vídeo+nome) ou vários seguidos (leva + 1 nome
            #     embaixo). Para no texto anterior (o nome da leva/par de cima).
            anteriores = [t for t in textos_brutos.get(chat_id, []) if t < mid]
            limite = max(anteriores) if anteriores else 0
            r, proximo = [], mid
            for x in db.exec("SELECT id, bruto_msg, status FROM videos WHERE chat_id=? AND bruto_msg < ? "
                             "AND bruto_msg > ? AND criado > ? ORDER BY bruto_msg DESC",
                             (chat_id, mid, limite, time.time() - 3600)):
                if proximo - x["bruto_msg"] > VIZINHANCA or x["status"] != "sem_produto":
                    break
                r.append(x)
                proximo = x["bruto_msg"]
            ativo = nome_ativo.get(chat_id)
            if r and ativo and ativo[2] == limite:
                # a leva acima veio DEPOIS de um nome mandado antes dela: é desse nome, não deste
                for x in r:
                    definir_produto(x["id"], ativo[0], ativo[1], None)
                r = []
            # 2º: digitado depois de o robô perguntar — vale pra todos os vídeos daquela pergunta
            if not r:
                ult = db.exec("SELECT pergunta_msg FROM videos WHERE chat_id=? AND status='sem_produto' "
                              "AND bruto_msg < ? AND pergunta_msg > 0 AND criado > ? "
                              "ORDER BY bruto_msg DESC LIMIT 1", (chat_id, mid, time.time() - 3600))
                if ult:
                    r = db.exec("SELECT id FROM videos WHERE status='sem_produto' AND pergunta_msg=?",
                                (ult[0]["pergunta_msg"],))
            for x in r:
                definir_produto(x["id"], nome, preco, seq if len(r) == 1 else None, nome_msg=mid)
            if r:
                tg_seguro("setMessageReaction", chat_id=chat_id, message_id=mid,
                          reaction=[{"type": "emoji", "emoji": "👌"}])
            else:
                # o vídeo de cima ainda não foi processado (corrida) ou é um nome mandado ANTES de uma leva
                soltos = [x for x in nomes_soltos.get(chat_id, []) if time.time() - x[3] < 600]
                soltos.append((mid, nome, seq, time.time()))
                nomes_soltos[chat_id] = soltos
                nome_ativo[chat_id] = (nome, preco, mid, time.time())
                tg_seguro("setMessageReaction", chat_id=chat_id, message_id=mid,
                          reaction=[{"type": "emoji", "emoji": "✍"}])
    elif thread in (topico("editados"), topico("hora")) and txt and resposta:
        # resposta com texto ao vídeo (ou ao aviso dele) = headline nova
        r = db.exec("SELECT * FROM videos WHERE (editado_msg=? AND status='aguardando') OR "
                    "((hora_msg=? OR aviso_msg=?) AND status IN ('agendado','na_hora'))",
                    (resposta, resposta, resposta))
        if r:
            fila.put(("headline", r[0]["id"], txt))
            tg_seguro("setMessageReaction", chat_id=chat_id, message_id=msg["message_id"],
                      reaction=[{"type": "emoji", "emoji": "👌"}])


def tratar_botao(cb):
    user = cb.get("from", {}).get("id")
    if user not in PERMITIDOS:
        tg_seguro("answerCallbackQuery", callback_query_id=cb["id"], text="Sem permissão")
        return
    acao, vid = cb["data"].split(":")
    v = db.video(int(vid))
    aviso = None
    if not v:
        aviso = "Vídeo não encontrado"
    elif acao == "ap" and v["status"] == "aguardando":
        aviso = f"Aprovado ✅ Vai pra ⏰ Hora de postar: {slot_bonito(agendar(v['id']))}"
    elif acao == "at" and v["status"] == "aguardando":
        todos = db.exec("SELECT id FROM videos WHERE chave=? AND status='aguardando' ORDER BY id", (v["chave"],))
        slots = sorted(agendar(x["id"]) for x in todos)
        aviso = f"{len(slots)} aprovados, de {slot_bonito(slots[0])} até {slot_bonito(slots[-1])}"
    elif acao == "ou" and v["status"] in ("aguardando", "agendado", "na_hora"):
        fila.put(("headline", v["id"], None))
        aviso = "Gerando outra headline..."
    elif acao == "de" and v["status"] in ("aguardando", "agendado", "na_hora"):
        db.atualizar(v["id"], status="descartado", slot=None)
        apagar_msgs(v["chat_id"], v["editado_msg"], v["hora_msg"], v["aviso_msg"])
        db.atualizar(v["id"], editado_msg=None, hora_msg=None, aviso_msg=None)
        aviso = "Descartado"
    elif acao == "po" and v["status"] in ("agendado", "na_hora"):
        quando = min(v["slot"], agora().strftime("%Y-%m-%d %H:%M"))   # postou antes: libera o horário
        db.atualizar(v["id"], status="postado", slot=quando)
        if v["hora_msg"]:
            tg_seguro("copyMessage", chat_id=v["chat_id"], from_chat_id=v["chat_id"],
                      message_id=v["hora_msg"], message_thread_id=topico("postados"), parse_mode="HTML",
                      caption=f"🚀 Postado {slot_bonito(quando)}\n🛍 {e(v['produto'])}\n📝 {e(v['headline'])}")
        apagar_msgs(v["chat_id"], v["hora_msg"], v["aviso_msg"], v["tiktok_msg"])
        db.atualizar(v["id"], hora_msg=None, aviso_msg=None, tiktok_msg=None)
        aviso = "Boa! 🚀"
    elif acao == "re" and v["status"] in ("agendado", "na_hora"):
        db.atualizar(v["id"], status="aguardando", slot=None)   # libera o horário atual
        slot = proximo_slot(v["id"])
        db.atualizar(v["id"], status="agendado", slot=slot)
        aviso = f"Reagendado: {slot_bonito(slot)}"
    elif acao == "tk" and v["status"] in ("agendado", "na_hora"):
        if conta_tiktok and conta_tiktok.conectada():
            fila.put(("tiktok", v["id"], None))
            aviso = "Mandando pro rascunho do TikTok… 📲"
        else:
            aviso = "TikTok não conectado — mande /tiktok"
    elif acao == "pa" and v["status"] == "agendado":
        # adianta: entra na agenda de hoje, agora
        db.atualizar(v["id"], slot=agora().strftime("%Y-%m-%d %H:%M"))
        aviso = "Foi pra agenda de hoje ⏰"
    else:
        aviso = "Esse botão já foi usado"
    tg_seguro("answerCallbackQuery", callback_query_id=cb["id"], text=aviso or "")
    if acao in ("ap", "at", "de", "po", "re", "pa"):
        sincronizar_agenda()


# =================================================================== main
def main():
    global db
    if not TOKEN:
        sys.exit("Defina TELEGRAM_BOT_TOKEN.")
    for prog in ("ffmpeg", "ffprobe"):
        if not shutil.which(prog):
            sys.exit(f"{prog} não encontrado.")
    DADOS.mkdir(parents=True, exist_ok=True)
    db = Banco(DADOS / "central.db")
    global conta_tiktok
    conta_tiktok = tiktok_api.Conta(DADOS / "tiktok.json")
    atualizar_glossario()
    eu = tg("getMe")
    print(f"Central TikTok: @{eu['username']} · permitidos {sorted(PERMITIDOS) or 'ninguém'}")

    for v in db.exec("SELECT id FROM videos WHERE status IN ('fila','processando') ORDER BY id"):
        fila.put(("processar", v["id"], None))
    threading.Thread(target=trabalhador, daemon=True).start()
    # primeira vez com a agenda por dia: apaga as mensagens soltas da versão anterior em ⏰ Hora de postar
    if db.get("agenda_pub") is None:
        for v in db.exec("SELECT * FROM videos WHERE status IN ('agendado','na_hora') AND hora_msg IS NOT NULL"):
            apagar_msgs(v["chat_id"], v["hora_msg"], v["aviso_msg"])
            db.atualizar(v["id"], hora_msg=None, aviso_msg=None)
    # versão anterior: aprovados ficavam com a mensagem em ✂️ Editados — tira de lá
    for v in db.exec("SELECT * FROM videos WHERE status IN ('agendado','na_hora') AND editado_msg IS NOT NULL"):
        apagar_msgs(v["chat_id"], v["editado_msg"])
        db.atualizar(v["id"], editado_msg=None)
    sincronizar_agenda()
    threading.Thread(target=agendador, daemon=True).start()

    offset = None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset
            for upd in tg("getUpdates", **params):
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd:
                        tratar_mensagem(upd["message"])
                    elif "callback_query" in upd:
                        tratar_botao(upd["callback_query"])
                except Exception:
                    traceback.print_exc()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as ex:
            print("rede:", ex)
            time.sleep(5)
        except Exception:
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
