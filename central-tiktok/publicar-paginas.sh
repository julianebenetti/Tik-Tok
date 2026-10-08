#!/usr/bin/env bash
# Publica as páginas que o TikTok exige (privacidade, termos, conectar) em
#   https://descontoirresistivel.com.br/central-tiktok/
# sem mexer no resto do site: acrescenta só um "location /central-tiktok/" no nginx,
# com cópia de segurança e teste antes de aplicar (se der erro, volta sozinho).
# Uso (root):  curl -fsSL .../central-tiktok/publicar-paginas.sh | bash
set -euo pipefail

DOMINIO="${DOMINIO:-descontoirresistivel.com.br}"
RAMO="${RAMO:-claude/ugc-tiktok-video-editing-7d11hx}"
BASE="https://raw.githubusercontent.com/julianebenetti/tik-tok/${RAMO}/central-tiktok/site"
PASTA=/var/www/central-tiktok

[ "$(id -u)" = 0 ] || { echo "Rode como root."; exit 1; }

echo "→ Copiando as páginas pra $PASTA ..."
mkdir -p "$PASTA"
for f in privacidade.html termos.html conectar.html estilo.css; do
  curl -fsSL "$BASE/$f" -o "$PASTA/$f"
done
chmod 755 "$PASTA"; chmod 644 "$PASTA"/*
echo "/var/www" > /etc/central-tiktok.site     # o instalador da Central passa a atualizar as páginas

CONF="$(grep -l "server_name $DOMINIO" /etc/nginx/sites-enabled/* | head -1)"
[ -n "$CONF" ] || { echo "❌ Não achei a configuração do nginx de $DOMINIO."; exit 1; }
CONF="$(readlink -f "$CONF")"

if grep -q "location /central-tiktok/" "$CONF"; then
  echo "→ O nginx já tem /central-tiktok/ — só atualizei as páginas."
else
  BACKUP="$CONF.antes-central-tiktok.$(date +%Y%m%d%H%M%S)"
  cp "$CONF" "$BACKUP"
  echo "→ Cópia de segurança: $BACKUP"
  # acrescenta o endereço antes do primeiro "location / {" (o do site em https)
  python3 - "$CONF" <<'PY'
import sys, re
p = sys.argv[1]; s = open(p).read()
bloco = ("location /central-tiktok/ {\n"
         "        alias /var/www/central-tiktok/;\n"
         "        add_header Cache-Control \"no-cache\";\n"
         "    }\n\n    ")
novo, n = re.subn(r"location / \{", bloco + "location / {", s, count=1)
if not n:
    sys.exit("não achei 'location / {'")
open(p, "w").write(novo)
PY
  if nginx -t 2>/tmp/nginx-teste.txt; then
    systemctl reload nginx
    echo "✅ nginx atualizado e recarregado."
  else
    cp "$BACKUP" "$CONF"
    echo "❌ O teste do nginx falhou — voltei a configuração original. Erro:"
    cat /tmp/nginx-teste.txt
    exit 1
  fi
fi

echo
echo "→ Testando os endereços (com certificado HTTPS):"
OK=""
for h in "$DOMINIO" "www.$DOMINIO" "financeiro.$DOMINIO"; do
  cod="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$h:443:127.0.0.1" \
        "https://$h/central-tiktok/privacidade.html" 2>/dev/null || true)"
  if [ "$cod" = "200" ]; then
    echo "   ✅ https://$h/central-tiktok/"; [ -z "$OK" ] && OK="$h"
  else
    echo "   ❌ https://$h/central-tiktok/  (resposta: ${cod:-erro de certificado})"
  fi
done
echo
if [ -n "$OK" ]; then
  echo "Use estes endereços no TikTok for Developers:"
  echo "   Política de privacidade:  https://$OK/central-tiktok/privacidade.html"
  echo "   Termos de uso:            https://$OK/central-tiktok/termos.html"
  echo "   Redirect URI (Login Kit): https://$OK/central-tiktok/conectar.html"
else
  echo "⚠️  Nenhum endereço respondeu. Mande um print desta tela pro Claude."
fi
