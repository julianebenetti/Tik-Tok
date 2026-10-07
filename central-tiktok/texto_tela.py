"""
Escreve a headline na tela do vídeo, no estilo do texto do TikTok:
letras pretas em faixas brancas arredondadas, centralizado no terço de cima.
"""
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

FONTES = [
    os.environ.get("FONTE_HEADLINE", ""),
    "/usr/share/fonts/opentype/montserrat/Montserrat-Bold.otf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def achar_fonte():
    for f in FONTES:
        if f and Path(f).exists():
            return f
    try:  # qualquer fonte negrito que o sistema tiver
        return subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"],
                              capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        raise RuntimeError("Nenhuma fonte encontrada — instale fonts-dejavu-core")


def tamanho(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def esc(caminho):
    """Escapa um caminho pra usar dentro de um filtro do ffmpeg."""
    return str(caminho).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def aplicar_headline(entrada, saida, texto, crf=20):
    w, h = tamanho(entrada)
    fonte = esc(achar_fonte())
    tam = round(w * 0.052)                     # ~37px num vídeo 720 de largura
    linhas = textwrap.wrap(texto, width=24, break_long_words=False) or [texto]
    passo = round(tam * 1.62)
    topo = round(h * 0.15)                     # abaixo do "Seguindo | Para você"
    with tempfile.TemporaryDirectory() as tmp:
        filtros = []
        for i, linha in enumerate(linhas):
            arq = Path(tmp) / f"l{i}.txt"
            arq.write_text(linha, encoding="utf-8")
            filtros.append(
                f"drawtext=fontfile='{fonte}':textfile='{esc(arq)}':expansion=none:"
                f"fontsize={tam}:fontcolor=black:box=1:boxcolor=white:"
                f"boxborderw={round(tam * 0.32)}:x=(w-text_w)/2:y={topo + i * passo}")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(entrada),
             "-vf", ",".join(filtros), "-an", "-c:v", "libx264", "-preset", "medium",
             "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(saida)],
            check=True, capture_output=True, text=True)
    return saida
