"""
Escreve a headline na tela do vídeo no estilo "Clássico" do texto do TikTok:
letra branca com contorno preto fino, sem fundo, centralizada um pouco abaixo do
meio do vídeo (como nos vídeos da @jubenettiindica). Emojis saem coloridos.

O texto é desenhado numa imagem transparente com Pillow (fonte TikTok Sans) e
colado no vídeo com o ffmpeg.
"""
import os
import subprocess
import tempfile
import unicodedata
from pathlib import Path

PASTA = Path(__file__).resolve().parent
FONTE_TEXTO = [
    os.environ.get("FONTE_HEADLINE", ""),
    str(PASTA / "fontes" / "TikTokSans.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONTE_EMOJI = [
    os.environ.get("FONTE_EMOJI", ""),
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
]

# Medidas tiradas de um vídeo da @jubenettiindica ("NÃO COMPRE esse vestido…"),
# em proporção da largura/altura do vídeo
TAMANHO = 0.049        # tamanho da letra = 4,9% da largura (altura da maiúscula ≈ 3,5%)
LARGURA_MAX = 0.76     # linha quebra quando passa de 76% da largura
ENTRELINHA = 1.13
CENTRO_Y = 0.61        # meio do bloco de texto a 61% da altura
CONTORNO = 0.055       # espessura do contorno em relação ao tamanho da letra
EIXOS = {"Optical size": 36, "Width": 110, "Weight": 550, "Slant": 0}   # TikTok Sans "Clássico"


def _primeira(caminhos):
    for c in caminhos:
        if c and Path(c).exists():
            return c
    return None


def tamanho(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


# ------------------------------------------------------------------ emojis
def _eh_emoji(ch):
    cp = ord(ch)
    return (cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF
            or (unicodedata.category(ch) == "So" and cp > 0x2000))


def _pedacos(palavra):
    """Divide em [('t', 'texto'), ('e', '👀'), ...] juntando sequências de emoji."""
    res, i = [], 0
    while i < len(palavra):
        ch = palavra[i]
        if _eh_emoji(ch):
            j = i + 1
            while j < len(palavra) and (palavra[j] in "️‍" or 0x1F3FB <= ord(palavra[j]) <= 0x1F3FF
                                        or (palavra[j - 1] == "‍" and _eh_emoji(palavra[j]))):
                j += 1
            res.append(("e", palavra[i:j]))
            i = j
        else:
            j = i
            while j < len(palavra) and not _eh_emoji(palavra[j]) and palavra[j] not in "️‍":
                j += 1
            if j == i:          # seletor solto
                j += 1
            else:
                res.append(("t", palavra[i:j]))
            i = j
    return res


class Desenhista:
    def __init__(self, fs):
        from PIL import ImageFont
        self.fs = fs
        caminho = _primeira(FONTE_TEXTO)
        if not caminho:
            raise RuntimeError("Fonte não encontrada — reinstale a Central (fontes/TikTokSans.ttf)")
        self.fonte = ImageFont.truetype(caminho, fs)
        try:
            eixos = self.fonte.get_variation_axes()
            self.fonte.set_variation_by_axes([EIXOS.get(a["name"].decode(), a["default"]) for a in eixos])
        except Exception:
            pass        # fonte sem eixos (DejaVu): usa como está
        emoji = _primeira(FONTE_EMOJI)
        self.fonte_emoji = ImageFont.truetype(emoji, 109) if emoji else None   # Noto só existe em 109
        self.contorno = max(1, round(fs * CONTORNO))
        self._cache = {}

    def emoji(self, seq):
        from PIL import Image, ImageDraw
        if seq in self._cache:
            return self._cache[seq]
        img = None
        if self.fonte_emoji:
            tmp = Image.new("RGBA", (220, 160), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text((10, 10), seq, font=self.fonte_emoji, embedded_color=True)
            caixa = tmp.getbbox()
            if caixa:
                tmp = tmp.crop(caixa)
                alt = round(self.fs * 1.0)
                img = tmp.resize((max(1, round(tmp.width * alt / tmp.height)), alt), Image.LANCZOS)
        self._cache[seq] = img
        return img

    def largura(self, palavra):
        total = 0
        for tipo, txt in _pedacos(palavra):
            if tipo == "t":
                total += self.fonte.getlength(txt)
            else:
                e = self.emoji(txt)
                total += e.width + self.fs * 0.08 if e else 0
        return total

    def quebrar(self, texto, maximo):
        linhas, atual = [], ""
        for p in texto.split():
            teste = f"{atual} {p}".strip()
            if atual and self.largura(teste) > maximo:
                linhas.append(atual)
                atual = p
            else:
                atual = teste
        if atual:
            linhas.append(atual)
        return linhas

    def desenhar_linha(self, img, linha, y, w):
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        x = (w - self.largura(linha)) / 2
        for tipo, txt in _pedacos(linha):
            if tipo == "t":
                d.text((x, y), txt, font=self.fonte, fill="white",
                       stroke_width=self.contorno, stroke_fill="black")
                x += self.fonte.getlength(txt)
            else:
                e = self.emoji(txt)
                if e:
                    x += self.fs * 0.04
                    img.alpha_composite(e, (round(x), round(y + self.fs * 0.12)))
                    x += e.width + self.fs * 0.04


def gerar_imagem(texto, w, h, saida):
    from PIL import Image
    fs = round(w * TAMANHO)
    dz = Desenhista(fs)
    linhas = dz.quebrar(texto, w * LARGURA_MAX)
    passo = round(fs * ENTRELINHA)
    topo = round(h * CENTRO_Y - passo * len(linhas) / 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for i, linha in enumerate(linhas):
        dz.desenhar_linha(img, " ".join(linha.split()), topo + i * passo, w)
    img.save(saida)
    return saida


def aplicar_headline(entrada, saida, texto, crf=20):
    w, h = tamanho(entrada)
    with tempfile.TemporaryDirectory() as tmp:
        png = gerar_imagem(texto, w, h, Path(tmp) / "headline.png")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(entrada), "-i", str(png),
             "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p", "-an",
             "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
             "-movflags", "+faststart", str(saida)],
            check=True, capture_output=True, text=True)
    return saida
