#!/usr/bin/env bash
# Varredura semanal do Espião TikTok, pra rodar no VPS via cron.
# Lê a chave do .env ao lado, nunca de dentro do código.
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$AQUI"

if [[ ! -f .env ]]; then
  echo "Falta o arquivo .env. Copie o vps/.env.exemplo e preencha." >&2
  exit 1
fi
set -a; source .env; set +a

: "${KALODATA_KEY:?defina KALODATA_KEY no .env}"
: "${SUPABASE_KEY:?defina SUPABASE_KEY no .env (a service_role, não a anônima)}"

PERIODO="${1:-7d}"
mkdir -p logs
LOG="logs/varredura-$(date +%Y-%m-%d).log"

{
  echo "===== $(date '+%d/%m/%Y %H:%M:%S') — período $PERIODO ====="

  if [[ -n "${CATEGORIA_MODA:-}" ]]; then
    echo "-- filtrando pela categoria $CATEGORIA_MODA --"
    CAT=(--filtrar-categoria "$CATEGORIA_MODA")
  else
    echo "-- sem CATEGORIA_MODA no .env: varrendo tudo --"
    CAT=()
  fi

  echo
  echo "### produtos que valem gravar (curadoria + ordenado por receita de vídeo)"
  python3 kalodata-api.py --ranking-produtos --curadoria \
      --ordenar-por video_revenue --periodo "$PERIODO" "${CAT[@]}"

  echo
  echo "### lojas que mais pagam afiliado"
  python3 kalodata-api.py --ranking-lojas \
      --ordenar-por affiliate_revenue --periodo "$PERIODO" "${CAT[@]}"

  echo
  echo "### criadores independentes"
  python3 kalodata-api.py --ranking-criadores --so-independentes \
      --periodo "$PERIODO" "${CAT[@]}"

  echo
  echo "===== fim: $(date '+%H:%M:%S') ====="
} 2>&1 | tee -a "$LOG"

# guarda os últimos 30 dias de log e joga o resto fora
find logs -name 'varredura-*.log' -mtime +30 -delete 2>/dev/null || true
