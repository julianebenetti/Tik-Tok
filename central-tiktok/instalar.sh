#!/usr/bin/env bash
# Instala (ou atualiza) a Central TikTok no VPS. Rode como root:
#   curl -fsSL https://raw.githubusercontent.com/julianebenetti/Tik-Tok/claude/ugc-tiktok-video-editing-7d11hx/central-tiktok/instalar.sh | bash
set -euo pipefail

RAMO="${RAMO:-claude/ugc-tiktok-video-editing-7d11hx}"
BASE="https://raw.githubusercontent.com/julianebenetti/Tik-Tok/${RAMO}/central-tiktok"
DIR=/opt/central-tiktok
ENV=/etc/central-tiktok.env

[ "$(id -u)" = 0 ] || { echo "Rode como root (ou com sudo)."; exit 1; }

echo "→ Instalando python3, ffmpeg e fontes..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 ffmpeg curl ca-certificates tzdata fontconfig fonts-dejavu-core >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fonts-montserrat >/dev/null 2>&1 || true

echo "→ Baixando a Central..."
mkdir -p "$DIR"
for f in central.py cortar_parados.py glossario.py glossario.json texto_tela.py; do
  curl -fsSL "$BASE/$f" -o "$DIR/$f"
done
id centraltiktok >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin centraltiktok

if [ ! -f "$ENV" ]; then
  echo
  echo "Cole o token que o @BotFather te deu (não aparece enquanto digita) e aperte Enter:"
  read -rs TOKEN < /dev/tty; echo
  echo "Agora o seu ID do Telegram (mande /start pro @userinfobot pra descobrir):"
  read -r IDS < /dev/tty
  umask 077
  printf 'TELEGRAM_BOT_TOKEN=%s\nUSUARIOS_PERMITIDOS=%s\n' "$TOKEN" "$IDS" > "$ENV"
  chmod 600 "$ENV"
fi

cat > /etc/systemd/system/central-tiktok.service <<UNIT
[Unit]
Description=Central TikTok (robô do Telegram)
After=network-online.target
Wants=network-online.target

[Service]
User=centraltiktok
EnvironmentFile=$ENV
Environment=CENTRAL_DADOS=/var/lib/central-tiktok
Environment=PYTHONUNBUFFERED=1
StateDirectory=central-tiktok
ExecStart=/usr/bin/python3 $DIR/central.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable central-tiktok >/dev/null 2>&1
systemctl restart central-tiktok
sleep 4
if systemctl is-active --quiet central-tiktok; then
  echo
  echo "✅ Central rodando!"
  journalctl -u central-tiktok -n 2 --no-pager -o cat
else
  echo "❌ A Central não subiu. Veja o erro:"
  journalctl -u central-tiktok -n 20 --no-pager -o cat
  exit 1
fi
