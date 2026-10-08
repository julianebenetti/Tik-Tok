#!/usr/bin/env bash
# Publica as páginas que o TikTok exige (privacidade, termos, conectar) em
#   https://posts.descontoirresistivel.com.br/central-tiktok/   (outro domínio: DOMINIO=... antes do bash)
# sem mexer no resto do site: acrescenta só um "location /central-tiktok/" no nginx,
# com cópia de segurança e teste antes de aplicar (se der erro, volta sozinho).
# Uso (root):  curl -fsSL .../central-tiktok/publicar-paginas.sh | bash
set -euo pipefail

DOMINIO="${DOMINIO:-posts.descontoirresistivel.com.br}"
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

# acha o arquivo e o bloco "server" HTTPS (listen 443) que atende exatamente esse domínio
CONF="$(python3 - "$DOMINIO" /etc/nginx/sites-enabled/* <<'PY'
import os, re, sys
dom, arqs = sys.argv[1], sys.argv[2:]
for a in arqs:
    try:
        s = open(a).read()
    except Exception:
        continue
    for m in re.finditer(r"server_name([^;]*);", s):
        if dom in m.group(1).split():
            print(os.path.realpath(a)); sys.exit()
PY
)"
[ -n "$CONF" ] || { echo "❌ Não achei a configuração do nginx de $DOMINIO."; exit 1; }
echo "→ Configuração: $CONF"

BACKUP="$CONF.antes-central-tiktok.$(date +%Y%m%d%H%M%S)"
cp "$CONF" "$BACKUP"
python3 - "$CONF" "$DOMINIO" <<'PY' || true
import re, sys
p, dom = sys.argv[1], sys.argv[2]
s = open(p).read()
# separa os blocos "server { ... }" contando chaves
blocos, i = [], 0
for m in re.finditer(r"\bserver\s*\{", s):
    if m.start() < i:
        continue
    prof, j = 0, m.end() - 1
    while j < len(s):
        prof += {"{": 1, "}": -1}.get(s[j], 0)
        if prof == 0:
            break
        j += 1
    blocos.append((m.start(), j + 1)); i = j + 1
alvo = None
for a, b in blocos:
    corpo = s[a:b]
    nomes = " ".join(x for x in re.findall(r"server_name([^;]*);", corpo)).split()
    if dom in nomes and re.search(r"listen[^;]*443", corpo):
        alvo = (a, b); break
if not alvo:
    sys.exit("SEM_HTTPS")
a, b = alvo
if "location /central-tiktok/" in s[a:b]:
    sys.exit(0)
pos = s.index("{", a) + 1
bloco = ("\n    location /central-tiktok/ {\n"
         "        alias /var/www/central-tiktok/;\n"
         "        add_header Cache-Control \"no-cache\";\n"
         "    }\n")
open(p, "w").write(s[:pos] + bloco + s[pos:])
PY
if ! grep -q "location /central-tiktok/" "$CONF"; then
  rm -f "$BACKUP"
  echo "❌ $DOMINIO não tem um bloco HTTPS (listen 443) nesse arquivo. Mande um print pro Claude."
  exit 1
fi
if cmp -s "$CONF" "$BACKUP"; then
  rm -f "$BACKUP"; echo "→ O nginx já tinha /central-tiktok/ — só atualizei as páginas."
elif nginx -t 2>/tmp/nginx-teste.txt; then
  systemctl reload nginx
  echo "→ Cópia de segurança: $BACKUP"
  echo "✅ nginx atualizado e recarregado."
else
  cp "$BACKUP" "$CONF"
  echo "❌ O teste do nginx falhou — voltei a configuração original. Erro:"
  cat /tmp/nginx-teste.txt
  exit 1
fi

echo
echo "→ Testando os endereços (com certificado HTTPS):"
OK=""
for h in "$DOMINIO"; do
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
