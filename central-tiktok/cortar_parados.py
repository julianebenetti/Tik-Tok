#!/usr/bin/env python3
"""
Corta automaticamente os trechos em que a modelo fica parada (estática) nos
vídeos UGC gerados por IA, pra ficarem com cara de pessoa real no TikTok.

Como funciona:
  1. O ffmpeg compara cada quadro com o anterior (diferença média de pixels).
  2. Trechos com movimento abaixo do limiar por mais de --min-parado segundos
     são considerados "modelo parada".
  3. Esses trechos são removidos (vídeo e áudio juntos) e o resto é colado,
     com um mini fade no áudio pra não estalar nos cortes.

Uso:
  python3 cortar_parados.py PASTA_OU_VIDEO [opções]

Exemplos:
  python3 cortar_parados.py videos/                  # processa a pasta toda
  python3 cortar_parados.py videos/ --analisar       # só mostra o que cortaria
  python3 cortar_parados.py video.mp4 --limiar 1.5   # mais agressivo

Requisito: ffmpeg e ffprobe instalados (nada de bibliotecas Python extras).
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

EXTENSOES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def sondar(video):
    """Retorna (duração em s, fps, tem_audio)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(video)],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    duracao = float(info["format"]["duration"])
    fps, tem_audio = 30.0, False
    for s in info["streams"]:
        if s["codec_type"] == "video":
            num, den = s.get("avg_frame_rate", "30/1").split("/")
            if float(den) > 0 and float(num) > 0:
                fps = float(num) / float(den)
        elif s["codec_type"] == "audio":
            tem_audio = True
    return duracao, fps, tem_audio


def medir_movimento(video):
    """Lista de (tempo, movimento) — movimento = diferença média entre quadros (0-255)."""
    filtro = ("scale=180:-2,format=gray,tblend=all_mode=difference,"
              "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-an", "-vf", filtro, "-f", "null", "-"],
        capture_output=True, text=True, check=True)
    pontos, t = [], None
    for linha in proc.stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", linha)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"YAVG=([\d.]+)", linha)
        if m and t is not None:
            pontos.append((t, float(m.group(1))))
    return pontos


def suavizar(valores, janela):
    """Média móvel centrada — ignora tremidinhas de um quadro só."""
    if janela <= 1:
        return valores[:]
    meia = janela // 2
    res = []
    for i in range(len(valores)):
        trecho = valores[max(0, i - meia):i + meia + 1]
        res.append(sum(trecho) / len(trecho))
    return res


def achar_parados(pontos, fps, duracao, limiar, min_parado, folga):
    """Retorna lista de (inicio, fim) dos trechos parados que serão cortados."""
    if not pontos:
        return []
    tempos = [p[0] for p in pontos]
    mov = suavizar([p[1] for p in pontos], max(1, round(fps * 0.2)))
    quadro = 1.0 / fps
    parados, inicio = [], None
    for t, m in zip(tempos, mov):
        if m < limiar:
            if inicio is None:
                inicio = t - quadro  # a diferença é medida em relação ao quadro anterior
        elif inicio is not None:
            parados.append((inicio, t - quadro))
            inicio = None
    if inicio is not None:
        parados.append((inicio, duracao))

    cortes = []
    for a, b in parados:
        a = max(0.0, a)
        b = min(duracao, b)
        if b - a < min_parado:
            continue
        # mantém uma folguinha nas bordas pra o corte não ficar seco,
        # exceto no começo/fim do vídeo, onde pode cortar tudo
        a2 = a if a <= quadro else a + folga
        b2 = b if b >= duracao - quadro else b - folga
        if b2 - a2 > quadro:
            cortes.append((a2, b2))
    return cortes


def trechos_mantidos(cortes, duracao, min_trecho):
    mantidos, pos = [], 0.0
    for a, b in cortes:
        if a - pos >= min_trecho:
            mantidos.append((pos, a))
        pos = b
    if duracao - pos >= min_trecho:
        mantidos.append((pos, duracao))
    return mantidos


def renderizar(video, saida, trechos, tem_audio, crf):
    partes, rotulos = [], []
    fade = 0.03
    for i, (a, b) in enumerate(trechos):
        partes.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        rotulos.append(f"[v{i}]")
        if tem_audio:
            d = b - a
            partes.append(
                f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:d={fade},afade=t=out:st={max(0, d - fade):.3f}:d={fade}[a{i}]")
            rotulos.append(f"[a{i}]")
    n = len(trechos)
    if tem_audio:
        partes.append("".join(rotulos) + f"concat=n={n}:v=1:a=1[v][a]")
        mapas = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    else:
        partes.append("".join(rotulos) + f"concat=n={n}:v=1:a=0[v]")
        mapas = ["-map", "[v]"]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
           "-filter_complex", ";".join(partes), *mapas,
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(saida)]
    subprocess.run(cmd, check=True)


def fmt(seg):
    return f"{seg:5.2f}s"


def processar(video, pasta_saida, args):
    duracao, fps, tem_audio = sondar(video)
    pontos = medir_movimento(video)
    cortes = achar_parados(pontos, fps, duracao, args.limiar, args.min_parado, args.folga)
    mantidos = trechos_mantidos(cortes, duracao, min_trecho=0.25)
    dur_final = sum(b - a for a, b in mantidos)

    print(f"\n▶ {video.name}  ({fmt(duracao)}, {fps:.0f} fps)")
    if args.analisar:
        mov = suavizar([p[1] for p in pontos], max(1, round(fps * 0.2)))
        passo = max(1, round(fps / 4))
        for (t, _), m in list(zip(pontos, mov))[::passo]:
            barra = "█" * min(40, int(m * 4))
            marca = "  ← parada" if m < args.limiar else ""
            print(f"   {t:6.2f}s  {m:5.2f} {barra}{marca}")
    for a, b in cortes:
        print(f"   ✂  corta {fmt(a)} → {fmt(b)}  ({b - a:.2f}s parada)")
    if not cortes:
        print("   ✓ nenhum trecho parado encontrado")

    status, saida = "analisado", None
    if not args.analisar:
        saida = pasta_saida / f"{video.stem}_editado.mp4"
        if not mantidos:
            print("   ⚠ vídeo inteiro parado — pulei")
            status, saida = "pulado (todo parado)", None
        elif not cortes:
            status, saida = "sem paradas", None
        elif dur_final < args.min_final:
            print(f"   ⚠ sobraria só {dur_final:.1f}s — pulei (ajuste --min-final)")
            status, saida = "pulado (ficaria curto)", None
        else:
            renderizar(video, saida, mantidos, tem_audio, args.crf)
            print(f"   💾 {saida.name}  ({fmt(duracao)} → {fmt(dur_final)})")
            status = "editado"
    return {
        "video": video.name, "duracao_original": round(duracao, 2),
        "duracao_final": round(dur_final, 2), "cortes": len(cortes),
        "tempo_cortado": round(duracao - dur_final, 2), "status": status,
        "trechos_cortados": " | ".join(f"{a:.2f}-{b:.2f}" for a, b in cortes),
        "saida": saida,
    }


def main():
    p = argparse.ArgumentParser(description="Corta trechos em que a modelo fica parada.")
    p.add_argument("entrada", help="vídeo ou pasta com vídeos")
    p.add_argument("-o", "--saida", help="pasta de saída (padrão: <entrada>/editados)")
    p.add_argument("--limiar", type=float, default=1.0,
                   help="movimento abaixo disso = parada (padrão 1.0; maior = corta mais)")
    p.add_argument("--min-parado", type=float, default=0.5,
                   help="só corta paradas com pelo menos X segundos (padrão 0.5)")
    p.add_argument("--folga", type=float, default=0.1,
                   help="segundos de parada mantidos em volta do corte (padrão 0.1)")
    p.add_argument("--min-final", type=float, default=3.0,
                   help="não salva se o vídeo final ficar menor que X segundos (padrão 3)")
    p.add_argument("--crf", type=int, default=18, help="qualidade H.264 (menor = melhor; padrão 18)")
    p.add_argument("--analisar", action="store_true",
                   help="só mostra o gráfico de movimento e os cortes, sem gerar vídeo")
    args = p.parse_args()

    entrada = Path(args.entrada)
    if entrada.is_dir():
        videos = sorted(f for f in entrada.iterdir()
                        if f.suffix.lower() in EXTENSOES and not f.stem.endswith("_editado"))
        base = entrada
    elif entrada.is_file():
        videos, base = [entrada], entrada.parent
    else:
        sys.exit(f"Não encontrei: {entrada}")
    if not videos:
        sys.exit("Nenhum vídeo encontrado.")

    pasta_saida = Path(args.saida) if args.saida else base / "editados"
    if not args.analisar:
        pasta_saida.mkdir(parents=True, exist_ok=True)

    relatorio = []
    for v in videos:
        try:
            relatorio.append(processar(v, pasta_saida, args))
        except subprocess.CalledProcessError as e:
            print(f"   ✗ erro no ffmpeg: {(e.stderr or '')[-300:]}")
            relatorio.append({"video": v.name, "status": "erro"})

    if not args.analisar:
        csv_path = pasta_saida / "relatorio.csv"
        campos = ["video", "status", "duracao_original", "duracao_final",
                  "tempo_cortado", "cortes", "trechos_cortados"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            w.writerows(relatorio)
        editados = sum(1 for r in relatorio if r.get("status") == "editado")
        print(f"\n✅ {editados}/{len(videos)} vídeos editados em {pasta_saida}")
        print(f"   relatório: {csv_path}")


if __name__ == "__main__":
    main()
