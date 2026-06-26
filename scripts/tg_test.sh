#!/usr/bin/env bash
# tg_test.sh — Send a test Telegram message to confirm bot + chat ID work.
#
# Usage: ./scripts/tg_test.sh
# Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from secrets.env.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${SCRIPT_DIR}/.."

if [ ! -f "${ROOT}/secrets.env" ]; then
  echo "Error: secrets.env not found. Copy secrets.env.example → secrets.env and fill it in."
  exit 1
fi

# shellcheck disable=SC1090
source "${ROOT}/secrets.env"

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set in secrets.env}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set in secrets.env}"

echo "Sending test ping to chat ${TELEGRAM_CHAT_ID}…"
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=🌱 Garden — test ping ($(date -u '+%Y-%m-%d %H:%M UTC'))" \
  | python3 -m json.tool

echo ""
echo "✓ If ok=true above, Telegram delivery is working."
