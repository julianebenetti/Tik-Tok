"""
Glossário da Central TikTok: headlines, hashtags e chamadas do link do Método UGC,
e as regras pra escolher cada uma a partir do nome do produto do TikTok.

Os TEXTOS ficam em glossario.json, que a rotina diária sincroniza com a página
"Prompts do Método UGC". O robô baixa a versão mais nova do GitHub a cada 6h
(se falhar, usa a última que deu certo). Aqui ficam só as regras.

Marcadores nas headlines:
  {produto}  termo curto do produto ("cropped", "calça", "vestido"...)
  {esse} {desse} {nesse} {o} {um} {do} {no}  concordam com o gênero do produto
  {o} também serve de terminação: "perfeit{o}" vira perfeito/perfeita ({O} maiúsculo)
  {preco}    preço exato   ("39,90")
  {menos}    preço arredondado pra cima ("40") — usado em "menos de X reais"

Validar um glossario.json:  python3 glossario.py --validar glossario.json
"""
import json
import os
import random
import re
import sys
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path

GLOSSARIO_URL = os.environ.get(
    "GLOSSARIO_URL",
    "https://raw.githubusercontent.com/julianebenetti/tik-tok/"
    "claude/ugc-tiktok-video-editing-7d11hx/central-tiktok/glossario.json")
LOCAL = Path(__file__).resolve().parent / "glossario.json"

# preenchidos por aplicar() a partir do glossario.json
HEADLINES = []          # (ângulo, texto, precisa_preco, so_roupa)
HASHTAGS = {}           # categoria -> [(palavras-chave, "4 hashtags")]
QUINTA_GENERICA = []
CHAMADA_ESPECIAL = {}
CHAMADA_GENERICA = "Loja • PROMO DO VÍDEO"
CHAMADA_PLUS = "Loja • PROMO PLUS SIZE"
CHAMADA_PADRAO = "Loja • PROMO {TERMO}"

# ------------------------------------------------------------------ produtos
# (palavras-chave sem acento, termo usado na headline, gênero m/f, categoria, é roupa)
# A ordem importa: o primeiro que bater ganha.
PRODUTOS = [
    (["biquini"], "biquíni", "m", "praia", True),
    (["maio"], "maiô", "m", "praia", True),
    (["saida de praia"], "saída de praia", "f", "praia", True),
    (["legging", "calca fitness"], "legging", "f", "baixo", True),
    (["conjunto fitness", "top fitness", "conjunto academia"], "conjunto fitness", "m", "fitness", True),
    (["macaquinho"], "macaquinho", "m", "conjunto", True),
    (["macacao"], "macacão", "m", "conjunto", True),
    (["conjunto", "conjuntinho"], "conjuntinho", "m", "conjunto", True),
    (["vestido"], "vestido", "m", "vestido", True),
    (["saia"], "saia", "f", "vestido", True),
    (["cropped"], "cropped", "m", "cima", True),
    (["body"], "body", "m", "cima", True),
    (["corset", "corselet", "corpete"], "corset", "m", "cima", True),
    (["regata"], "regata", "f", "cima", True),
    (["camiseta", "t-shirt", "tshirt"], "camiseta", "f", "cima", True),
    (["camisa"], "camisa", "f", "cima", True),
    (["jaqueta"], "jaqueta", "f", "cima", True),
    (["casaco", "sobretudo"], "casaco", "m", "cima", True),
    (["cardigan", "cardiga"], "cardigã", "m", "cima", True),
    (["moletom"], "moletom", "m", "cima", True),
    (["blazer"], "blazer", "m", "cima", True),
    (["colete"], "colete", "m", "cima", True),
    (["blusa", "blusinha"], "blusinha", "f", "cima", True),
    (["top"], "top", "m", "cima", True),
    (["calca"], "calça", "f", "baixo", True),
    (["bermuda"], "bermuda", "f", "baixo", True),
    (["short"], "short", "m", "baixo", True),
    (["rasteirinha", "rasteira"], "rasteirinha", "f", "calcado", False),
    (["sandalia"], "sandália", "f", "calcado", False),
    (["tenis"], "tênis", "m", "calcado", False),
    (["bota", "coturno"], "bota", "f", "calcado", False),
    (["sapatilha"], "sapatilha", "f", "calcado", False),
    (["scarpin", "salto"], "scarpin", "m", "calcado", False),
    (["chinelo"], "chinelo", "m", "calcado", False),
    (["mochila"], "mochila", "f", "bolsa", False),
    (["bolsa"], "bolsa", "f", "bolsa", False),
    (["colar"], "colar", "m", "acessorio", False),
    (["brinco"], "brinco", "m", "acessorio", False),
    (["pulseira"], "pulseira", "f", "acessorio", False),
    (["oculos"], "óculos", "m", "acessorio", False),
    (["cinto"], "cinto", "m", "acessorio", False),
    (["relogio"], "relógio", "m", "acessorio", False),
]
GENERICO = ("achadinho", "m", "geral", False)

# ------------------------------------------------------------------ carregar
VALORES_TESTE = dict(produto="x", esse="", desse="", nesse="", o="", O="", um="",
                     do="", no="", preco="", menos="")


def validar(d):
    """Confere o glossario.json. Levanta ValueError dizendo o que está errado."""
    erros = []
    hs = d.get("headlines") or []
    if len(hs) < 5:
        erros.append("menos de 5 headlines")
    for i, h in enumerate(hs):
        try:
            h["texto"].format(**VALORES_TESTE)
            assert isinstance(h["angulo"], str) and h["angulo"]
        except Exception as ex:
            erros.append(f"headline {i + 1} ({h.get('texto', '?')!r}): marcador inválido {ex}")
        if ("{menos}" in h.get("texto", "") or "{preco}" in h.get("texto", "")) and not h.get("precisa_preco"):
            erros.append(f"headline {i + 1}: usa preço mas precisa_preco=false")
    tags = d.get("hashtags") or {}
    if "geral" not in tags:
        erros.append("falta a categoria 'geral' nas hashtags")
    for cat, sets in tags.items():
        if not sets:
            erros.append(f"categoria {cat} sem conjuntos")
        for s in sets:
            t = s.get("tags", "").split()
            if len(t) != 4 or not all(x.startswith("#") for x in t):
                erros.append(f"{cat}: conjunto precisa ter 4 hashtags: {s.get('tags')!r}")
    if len(d.get("quinta_generica") or []) < 2:
        erros.append("quinta_generica precisa de pelo menos 2 hashtags")
    if not (d.get("chamadas") or {}).get("generica"):
        erros.append("falta chamadas.generica")
    if erros:
        raise ValueError("; ".join(erros))
    return d


def aplicar(d):
    global HEADLINES, HASHTAGS, QUINTA_GENERICA, CHAMADA_ESPECIAL
    global CHAMADA_GENERICA, CHAMADA_PLUS, CHAMADA_PADRAO
    validar(d)
    HEADLINES = [(h["angulo"], h["texto"], bool(h.get("precisa_preco")), bool(h.get("so_roupa")))
                 for h in d["headlines"]]
    HASHTAGS = {cat: [(s.get("chaves") or [], s["tags"]) for s in sets]
                for cat, sets in d["hashtags"].items()}
    QUINTA_GENERICA = list(d["quinta_generica"])
    ch = d["chamadas"]
    CHAMADA_ESPECIAL = dict(ch.get("por_produto") or {})
    CHAMADA_GENERICA = ch["generica"]
    CHAMADA_PLUS = ch.get("plus_size", CHAMADA_PLUS)
    CHAMADA_PADRAO = ch.get("padrao", CHAMADA_PADRAO)


def carregar(cache=None, url=GLOSSARIO_URL):
    """Baixa o glossário do GitHub; se falhar, usa o cache ou o arquivo local.
    Retorna de onde veio e quantas headlines tem."""
    tentativas = []
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = validar(json.load(r))
        if cache:
            Path(cache).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        aplicar(d)
        return "github", len(HEADLINES)
    except Exception as ex:
        tentativas.append(f"github: {ex}")
    for nome, arq in (("cache", cache), ("local", LOCAL)):
        try:
            if arq and Path(arq).exists():
                aplicar(json.loads(Path(arq).read_text(encoding="utf-8")))
                return nome, len(HEADLINES)
        except Exception as ex:
            tentativas.append(f"{nome}: {ex}")
    raise RuntimeError("Não consegui carregar o glossário — " + " | ".join(tentativas))


# ------------------------------------------------------------------ lógica
def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def tem(n, chaves):
    """Alguma palavra-chave aparece como palavra (aceita plural)?"""
    return any(re.search(r"(?<![a-z])" + re.escape(k) + r"(s|es)?(?![a-z])", n) for k in chaves)


def identificar(nome):
    """Nome do produto do TikTok → dict com termo, gênero, categoria, roupa."""
    n = " " + sem_acento(nome) + " "
    termo, genero, categoria, roupa = GENERICO
    for chaves, t, g, cat, r in PRODUTOS:
        if tem(n, chaves):
            termo, genero, categoria, roupa = t, g, cat, r
            break
    if "plus size" in n or "plussize" in n or " plus " in n:
        categoria = "plussize"
    return {"termo": termo, "genero": genero, "categoria": categoria, "roupa": roupa, "nome_norm": n}


def temporada(hoje=None):
    """Hashtag de data comemorativa, se houver uma agora (5ª hashtag e chamada)."""
    hoje = hoje or date.today()
    m, d, a = hoje.month, hoje.day, hoje.year
    if m == 11:
        return "#blackfriday", "Loja • BLACK FRIDAY"
    if m == 12 and d <= 25:
        return "#natal", "Loja • PROMO DE NATAL"
    if (m == 12 and d >= 26) or (m == 1 and d <= 2):
        return "#anonovo", None
    if m in (1, 2) or (m == 12) or (m == 3 and d <= 20):
        ano = a + 1 if m == 12 else a
        return f"#verao{ano}", "Loja • LOOK DE VERÃO"
    return None, None


def hashtags(info, hoje=None, rnd=random):
    sets = HASHTAGS.get(info["categoria"], HASHTAGS["geral"])
    especificos = [h for chaves, h in sets if chaves and tem(info["nome_norm"], chaves)]
    genericos = [h for chaves, h in sets if not chaves]
    tags = (especificos[0] if especificos else rnd.choice(genericos or [h for _, h in sets])).split()
    quinta, _ = temporada(hoje)
    if not quinta or quinta in tags:
        quinta = next(h for h in QUINTA_GENERICA if h not in tags)
    return " ".join(tags + [quinta])


def chamada(info, hoje=None):
    _, sazonal = temporada(hoje)
    if sazonal:
        return sazonal
    if info["termo"] == GENERICO[0]:
        return CHAMADA_GENERICA
    if info["categoria"] == "plussize":
        return CHAMADA_PLUS
    return CHAMADA_ESPECIAL.get(info["termo"], CHAMADA_PADRAO.replace("{TERMO}", info["termo"].upper()))


def preencher(texto, info, preco):
    m = info["genero"] == "m"
    vals = {
        "produto": info["termo"],
        "esse": "esse" if m else "essa", "desse": "desse" if m else "dessa",
        "nesse": "nesse" if m else "nessa", "o": "o" if m else "a", "O": "O" if m else "A",
        "um": "um" if m else "uma", "do": "do" if m else "da", "no": "no" if m else "na",
        "preco": "", "menos": "",
    }
    if preco:
        vals["preco"] = (f"{preco:.2f}".replace(".", ",")).replace(",00", "")
        vals["menos"] = str(int(preco // 5 * 5 + 5))  # 39,90 → 40 · 40 → 45
    return texto.format(**vals)


def opcoes_headline(info, preco):
    return [(i, ang) for i, (ang, _, precisa_preco, so_roupa) in enumerate(HEADLINES)
            if (preco or not precisa_preco) and (info["roupa"] or not so_roupa)]


def escolher_headline(info, preco, evitar_angulo=None, ja_usadas=(), rnd=random):
    """Retorna (indice, angulo, texto). Evita repetir o ângulo do vídeo anterior."""
    ops = [o for o in opcoes_headline(info, preco) if o[0] not in ja_usadas] \
        or opcoes_headline(info, preco)
    melhores = [o for o in ops if o[1] != evitar_angulo] or ops
    angulo = rnd.choice(sorted({a for _, a in melhores}))
    i = rnd.choice([idx for idx, a in melhores if a == angulo])
    return i, angulo, preencher(HEADLINES[i][1], info, preco)


def ler_legenda(texto):
    """'Cropped canelado manga longa | 39,90' → ('Cropped canelado manga longa', 39.9)."""
    if not texto:
        return None, None
    partes = [p.strip() for p in texto.strip().split("|")]
    nome, preco = partes[0], None
    if len(partes) > 1:
        bruto = partes[1].lower().replace("r$", "").replace("reais", "").strip()
        bruto = bruto.replace(".", "").replace(",", ".") if "," in bruto else bruto
        try:
            preco = float(bruto)
        except ValueError:
            preco = None
    return (nome or None), preco



# Nome de arquivo do gerador de vídeos, ex.:
#   CONJUNTO_BIQUINI_FEMININO_COM_SAIDA_G1C2A1_0510_1791245116165.mp4
#   → produto "Conjunto biquini feminino com saida", sequência "G1C2A1"
RE_ARQUIVO = re.compile(r"^\s*([A-Za-z0-9À-ÿ_\-]+?)\.(mp4|mov|m4v|webm)\s*$", re.I)
RE_SEQUENCIA = re.compile(r"^G\d+C\d+(A|CTA)\d+$", re.I)


def ler_nome_arquivo(texto):
    """Retorna (produto, sequencia) se o texto for um nome de arquivo de vídeo, senão None."""
    m = RE_ARQUIVO.match(texto or "")
    if not m:
        return None
    partes = [p for p in re.split(r"[_\-]+", m.group(1)) if p]
    sequencia = None
    # tira do fim: números (timestamp, data) e o código G#C#A#
    while partes and (partes[-1].isdigit() or RE_SEQUENCIA.match(partes[-1])):
        p = partes.pop()
        if RE_SEQUENCIA.match(p):
            sequencia = p.upper()
    if not partes or not any(c.isalpha() for c in "".join(partes)):
        return None
    # sem o código G#C#A#, só aceita no formato do gerador (MAIÚSCULAS_COM_UNDERLINE)
    if not sequencia and not (len(partes) >= 2 and m.group(1).isupper()):
        return None
    nome = " ".join(partes).lower()
    return nome[0].upper() + nome[1:], sequencia


def chave_produto(nome):
    """Mesma chave pra variações de escrita do mesmo produto."""
    return re.sub(r"[^a-z0-9]+", " ", sem_acento(nome or "")).strip()

aplicar(json.loads(LOCAL.read_text(encoding="utf-8")))

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--validar":
        arq = Path(sys.argv[2] if len(sys.argv) > 2 else LOCAL)
        try:
            aplicar(json.loads(arq.read_text(encoding="utf-8")))
        except Exception as ex:
            sys.exit(f"❌ {arq}: {ex}")
        print(f"✅ {arq}: {len(HEADLINES)} headlines, "
              f"{sum(len(v) for v in HASHTAGS.values())} conjuntos de hashtags")
        for nome in ("Calça wide leg | 79,90", "Cropped canelado", "Vestido longo plus size"):
            n, p = ler_legenda(nome)
            info = identificar(n)
            for idx, _ in opcoes_headline(info, p):
                preencher(HEADLINES[idx][1], info, p)   # garante que todas preenchem
            print(f"   {n}: {escolher_headline(info, p)[2]} | {hashtags(info)} | {chamada(info)}")
