#!/usr/bin/env python3
"""
Central TikTok — robô do Telegram que organiza os vídeos UGC da Juliane.

Grupo do Telegram com tópicos:
  📥 Brutos         você manda o vídeo cru com a legenda "nome do produto | preço"
  ✂️ Editados       o robô corta as paradas, escreve a headline na tela, sugere
                    5 hashtags + chamada do link e pede aprovação
  ⏰ Hora de postar no horário agendado (17h–22h, horários quebrados) o vídeo
                    chega aqui pronto pra postar
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

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PERMITIDOS = {int(x) for x in os.environ.get("USUARIOS_PERMITIDOS", "").replace(" ", "").split(",") if x}
DADOS = Path(os.environ.get("CENTRAL_DADOS", Path.home() / ".central-tiktok"))
FUSO = ZoneInfo(os.environ.get("CENTRAL_FUSO", "America/Sao_Paulo"))
API = f"https://api.telegram.org/bot{TOKEN}"
ARQ_API = f"https://api.telegram.org/file/bot{TOKEN}"
LIMITE_DOWNLOAD = 20 * 1024 * 1024

POSTS_POR_DIA = 6
JANELA = (17 * 60, 22 * 60)          # 17h às 22h, em minutos
DIAS_GUARDAR = 3                      # apaga arquivos de postados/descartados depois disso
TOPICOS = [("brutos", "📥 Brutos"), ("editados", "✂️ Editados"),
           ("hora", "⏰ Hora de postar"), ("postados", "🚀 Postados")]
DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

fila = queue.Queue()
legendas_album = {}                   # media_group_id -> legenda do álbum


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


# =================================================================== Telegram
def tg(metodo, **params):
    req = urllib.request.Request(f"{API}/{metodo}", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        resp = json.load(e)
    if not resp.get("ok"):
        raise RuntimeError(f"{metodo}: {resp.get('description', resp)}")
    return resp["result"]


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
    req = urllib.request.Request(f"{API}/{metodo}", data=bytes(corpo),
                                 headers={"Content-Type": f"multipart/form-data; boundary={fronteira}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        resp = json.load(e)
    if not resp.get("ok"):
        raise RuntimeError(f"{metodo}: {resp.get('description', resp)}")
    return resp["result"]


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


def proximo_slot():
    ocupados = {r["slot"] for r in db.exec(
        "SELECT slot FROM videos WHERE slot IS NOT NULL AND status IN ('agendado','na_hora','postado')")}
    minimo = agora() + timedelta(minutes=5)
    dia = minimo.date()
    for _ in range(120):
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


def legenda_editado(v, extra=""):
    if v["cortes"]:
        corte = f"✂️ {v['cortes']} corte(s) · {v['dur_orig']:.1f}s → {v['dur_final']:.1f}s"
    else:
        corte = "✂️ sem paradas pra cortar"
    return (f"🛍 <b>{e(v['produto'])}</b> · {preco_txt(v['preco'])}\n\n"
            f"📝 <b>Na tela:</b> {e(v['headline'])}\n\n"
            f"#️⃣ <code>{e(v['hashtags'])}</code>\n"
            f"🔗 <code>{e(v['chamada'])}</code>\n\n"
            f"{corte}\n"
            f"💬 <i>Responda este vídeo com um texto pra usar outra headline.</i>"
            + (f"\n\n{extra}" if extra else ""))


def botoes_aprovacao(vid):
    return botoes([("✅ Aprovar", f"ap:{vid}"), ("🔄 Outra headline", f"ou:{vid}")],
                  [("❌ Descartar", f"de:{vid}")])


def legenda_hora(v):
    return (f"⏰ <b>Hora de postar!</b> ({slot_bonito(v['slot'])})\n\n"
            f"1️⃣ Salve o vídeo e poste no TikTok\n"
            f"2️⃣ Legenda (toque pra copiar):\n<code>{e(v['hashtags'])}</code>\n"
            f"3️⃣ Produto no TikTok Shop: <b>{e(v['produto'])}</b>\n"
            f"4️⃣ Chamada do link: <code>{e(v['chamada'])}</code>\n\n"
            f"Depois toque em <b>Postei</b> 👇")


# =================================================================== processamento
def pasta_video(vid):
    p = DADOS / "videos" / str(vid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def nova_headline(v, info, evitar=None):
    usadas = json.loads(v["usadas"] or "[]")
    idx, ang, txt = glossario.escolher_headline(info, v["preco"], evitar_angulo=evitar, ja_usadas=usadas)
    return idx, ang, txt, json.dumps(usadas + [idx])


def enviar_editado(vid):
    v = db.video(vid)
    res = tg_arquivo("sendVideo", "video", v["final"], chat_id=v["chat_id"],
                     message_thread_id=topico("editados"), caption=legenda_editado(v),
                     parse_mode="HTML", supports_streaming="true",
                     reply_markup=botoes_aprovacao(vid))
    antigo = v["editado_msg"]
    db.atualizar(vid, editado_msg=res["message_id"], status="aguardando")
    if antigo:
        tg_seguro("deleteMessage", chat_id=v["chat_id"], message_id=antigo)


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
                 base=str(base), final=str(final))
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
    db.atualizar(vid, headline=headline, angulo=ang, usadas=usadas)
    enviar_editado(vid)


def trabalhador():
    while True:
        tarefa, vid, extra = fila.get()
        try:
            if tarefa == "processar":
                processar(vid)
            elif tarefa == "headline":
                refazer_headline(vid, extra)
        except Exception as ex:
            traceback.print_exc()
            v = db.video(vid)
            if tarefa == "processar":
                db.atualizar(vid, status="erro")
            if v:
                texto(v["chat_id"], f"❌ Deu erro neste vídeo: {e(str(ex)[:300])}",
                      "brutos" if tarefa == "processar" else "editados",
                      v["bruto_msg"] if tarefa == "processar" else v["editado_msg"])
        finally:
            fila.task_done()


# =================================================================== agendador
def hora_de_postar(v):
    res = tg_arquivo("sendVideo", "video", v["final"], chat_id=v["chat_id"],
                     message_thread_id=topico("hora"), caption=legenda_hora(v), parse_mode="HTML",
                     supports_streaming="true",
                     reply_markup=botoes([("✅ Postei", f"po:{v['id']}"),
                                          ("🔁 Reagendar", f"re:{v['id']}")]))
    db.atualizar(v["id"], status="na_hora", hora_msg=res["message_id"])


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
            for v in db.exec("SELECT * FROM videos WHERE status='sem_produto' AND pergunta_msg IS NULL "
                             "AND criado < ?", (time.time() - 4,)):
                leg = legendas_album.get(v["album"]) if v["album"] else None
                nome, preco = glossario.ler_legenda(leg)
                if nome:
                    db.atualizar(v["id"], produto=nome, preco=preco, status="fila")
                    fila.put(("processar", v["id"], None))
                else:
                    m = texto(v["chat_id"], "🛍 Qual o produto deste vídeo? <b>Responda esta mensagem</b> com:\n"
                                            "<code>nome do produto no TikTok | preço</code>",
                              "brutos", v["bruto_msg"])
                    db.atualizar(v["id"], pergunta_msg=m["message_id"] if m else 0)
            # 2) horários que chegaram
            for v in db.exec("SELECT * FROM videos WHERE status='agendado' AND slot <= ?",
                             (agora().strftime("%Y-%m-%d %H:%M"),)):
                try:
                    hora_de_postar(v)
                except Exception:
                    traceback.print_exc()
            # 3) limpeza dos arquivos antigos
            if time.time() - ultima_limpeza > 3600:
                ultima_limpeza = time.time()
                for v in db.exec("SELECT id FROM videos WHERE status IN ('postado','descartado','erro') "
                                 "AND atualizado < ?", (time.time() - DIAS_GUARDAR * 86400,)):
                    shutil.rmtree(DADOS / "videos" / str(v["id"]), ignore_errors=True)
        except Exception:
            traceback.print_exc()
        time.sleep(15)


# =================================================================== mensagens
AJUDA = (
    "🎬 <b>Central TikTok</b>\n\n"
    "<b>1.</b> Mande o vídeo cru em <b>📥 Brutos</b> com a legenda:\n"
    "<code>nome do produto no TikTok | preço</code>\n"
    "(o preço é opcional; vários vídeos do mesmo produto podem ir num álbum só)\n\n"
    "<b>2.</b> Em <b>✂️ Editados</b> eu devolvo cortado, com headline na tela, hashtags e "
    "chamada. Aprove, peça outra headline ou responda o vídeo com a sua.\n\n"
    "<b>3.</b> Os aprovados entram na agenda (6 por dia, 17h–22h). No horário, o vídeo chega "
    "em <b>⏰ Hora de postar</b>. Postou? Toque em <b>Postei</b>.\n\n"
    "Comandos: /agenda · /glossario (recarrega headlines e hashtags) · /limiar 1.5 (corta mais) · "
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
            nome, preco = glossario.ler_legenda(leg)
            db.atualizar(v["id"], produto=nome, preco=preco, status="fila")
            fila.put(("processar", v["id"], None))
    nome, preco = glossario.ler_legenda(leg or legendas_album.get(album))
    status = "fila" if nome else "sem_produto"
    db.exec("INSERT INTO videos (status, chat_id, bruto_msg, file_id, album, produto, preco, criado, atualizado) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (status, msg["chat"]["id"], msg["message_id"], file_id, album, nome, preco, time.time(), time.time()))
    vid = db.exec("SELECT last_insert_rowid() AS id")[0]["id"]
    if nome:
        fila.put(("processar", vid, None))


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
        linhas = [f"• {slot_bonito(v['slot'])} — {e(v['produto'])}" for v in db.exec(
            "SELECT * FROM videos WHERE status='agendado' ORDER BY slot")]
        aguardando = db.exec("SELECT COUNT(*) AS n FROM videos WHERE status='aguardando'")[0]["n"]
        corpo = "\n".join(linhas) or "Nada agendado ainda."
        texto(chat_id, f"📅 <b>Agenda</b>\n{corpo}\n\n⏳ Esperando aprovação: {aguardando}",
              responder=msg["message_id"], message_thread_id=msg.get("message_thread_id"))
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
        if video:
            receber_video(msg, video)
        elif txt and resposta:
            # resposta à pergunta "qual o produto?" (ou direto ao vídeo)
            r = db.exec("SELECT * FROM videos WHERE status='sem_produto' AND (pergunta_msg=? OR bruto_msg=?)",
                        (resposta, resposta))
            if r:
                nome, preco = glossario.ler_legenda(txt)
                db.atualizar(r[0]["id"], produto=nome, preco=preco, status="fila")
                fila.put(("processar", r[0]["id"], None))
                tg_seguro("setMessageReaction", chat_id=chat_id, message_id=msg["message_id"],
                          reaction=[{"type": "emoji", "emoji": "👌"}])
    elif thread == topico("editados") and txt and resposta:
        r = db.exec("SELECT * FROM videos WHERE editado_msg=? AND status='aguardando'", (resposta,))
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
        slot = proximo_slot()
        db.atualizar(v["id"], status="agendado", slot=slot)
        tg_seguro("editMessageCaption", chat_id=v["chat_id"], message_id=v["editado_msg"],
                  caption=legenda_editado(db.video(v["id"]), f"📅 <b>Agendado: {slot_bonito(slot)}</b>"),
                  parse_mode="HTML", reply_markup=botoes([("↩️ Desagendar", f"da:{v['id']}")]))
        aviso = f"Agendado: {slot_bonito(slot)}"
    elif acao == "ou" and v["status"] == "aguardando":
        fila.put(("headline", v["id"], None))
        aviso = "Gerando outra headline..."
    elif acao == "de" and v["status"] in ("aguardando", "agendado"):
        db.atualizar(v["id"], status="descartado", slot=None)
        tg_seguro("editMessageCaption", chat_id=v["chat_id"], message_id=v["editado_msg"],
                  caption=f"❌ Descartado — {e(v['produto'])}", parse_mode="HTML")
        aviso = "Descartado"
    elif acao == "da" and v["status"] == "agendado":
        db.atualizar(v["id"], status="aguardando", slot=None)
        tg_seguro("editMessageCaption", chat_id=v["chat_id"], message_id=v["editado_msg"],
                  caption=legenda_editado(v), parse_mode="HTML", reply_markup=botoes_aprovacao(v["id"]))
        aviso = "Voltou pra aprovação"
    elif acao == "po" and v["status"] == "na_hora":
        db.atualizar(v["id"], status="postado")
        tg_seguro("copyMessage", chat_id=v["chat_id"], from_chat_id=v["chat_id"],
                  message_id=v["hora_msg"], message_thread_id=topico("postados"), parse_mode="HTML",
                  caption=f"🚀 Postado {slot_bonito(v['slot'])}\n🛍 {e(v['produto'])}\n📝 {e(v['headline'])}")
        tg_seguro("deleteMessage", chat_id=v["chat_id"], message_id=v["hora_msg"])
        aviso = "Boa! 🚀"
    elif acao == "re" and v["status"] == "na_hora":
        db.atualizar(v["id"], status="aguardando")       # libera o horário atual
        slot = proximo_slot()
        db.atualizar(v["id"], status="agendado", slot=slot)
        tg_seguro("deleteMessage", chat_id=v["chat_id"], message_id=v["hora_msg"])
        aviso = f"Reagendado: {slot_bonito(slot)}"
    else:
        aviso = "Esse botão já foi usado"
    tg_seguro("answerCallbackQuery", callback_query_id=cb["id"], text=aviso or "")


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
    atualizar_glossario()
    eu = tg("getMe")
    print(f"Central TikTok: @{eu['username']} · permitidos {sorted(PERMITIDOS) or 'ninguém'}")

    for v in db.exec("SELECT id FROM videos WHERE status IN ('fila','processando') ORDER BY id"):
        fila.put(("processar", v["id"], None))
    threading.Thread(target=trabalhador, daemon=True).start()
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
