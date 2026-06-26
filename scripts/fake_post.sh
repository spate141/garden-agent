#!/usr/bin/env bash
# fake_post.sh — Send a synthetic Ecowitt POST to the local ingest endpoint.
#
# Usage:
#   ./scripts/fake_post.sh [HOST] [PASSKEY]
#
# Defaults:
#   HOST    = http://127.0.0.1:8000
#   PASSKEY = from secrets.env (if present), else 'test_passkey'
#
# To trip soil-moisture-low for bed 1 (below 30%), pass LOW=1:
#   LOW=1 ./scripts/fake_post.sh

set -euo pipefail

HOST="${1:-http://127.0.0.1:8000}"

# Load PASSKEY from secrets.env if available, else fall back to arg or env.
if [ -f "$(dirname "$0")/../secrets.env" ]; then
    PASSKEY="$(grep '^INGEST_PASSKEY=' "$(dirname "$0")/../secrets.env" | cut -d= -f2- | tr -d '"'\'' ')"
fi
PASSKEY="${2:-${PASSKEY:-test_passkey}}"

# Normal readings (healthy garden)
SOIL1=55
SOIL2=48
TEMP=22.0
if [ "${LOW:-0}" = "1" ]; then
    SOIL1=22
    SOIL2=18
fi

NOW="$(date -u '+%Y-%m-%d %H:%M:%S')"

echo "→ POSTing to ${HOST}/api/ecowitt (PASSKEY=${PASSKEY})"
echo "  soilmoisture1=${SOIL1}%  soilmoisture2=${SOIL2}%  tempf=$(echo "${TEMP} * 9/5 + 32" | bc -l | xargs printf '%.1f')"
echo ""

curl -sf -X POST "${HOST}/api/ecowitt" \
  --data-urlencode "PASSKEY=${PASSKEY}" \
  --data-urlencode "stationtype=GW1200A_V2.1.4" \
  --data-urlencode "dateutc=${NOW}" \
  --data-urlencode "tempf=71.6" \
  --data-urlencode "humidity=62" \
  --data-urlencode "tempinf=73.4" \
  --data-urlencode "humidityin=55" \
  --data-urlencode "baromrelin=29.92" \
  --data-urlencode "baromabsin=29.88" \
  --data-urlencode "soilmoisture1=${SOIL1}" \
  --data-urlencode "soilmoisture2=${SOIL2}" \
  --data-urlencode "soilbatt1=1.5" \
  --data-urlencode "soilbatt2=1.4" \
  | python3 -m json.tool

echo ""
echo "✓ Done."
