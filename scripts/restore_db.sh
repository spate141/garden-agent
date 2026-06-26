#!/usr/bin/env bash
# restore_db.sh — Download the latest DB backup from Cloudflare R2.
#
# Usage:
#   bash scripts/restore_db.sh              # downloads garden-latest.sqlite3
#   bash scripts/restore_db.sh 20260625     # downloads garden-20260625.sqlite3
#
# Prerequisites: rclone installed, R2_* vars filled in secrets.env.
# After restore, restart garden-agent: sudo systemctl restart garden-agent

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

# Load secrets
if [ ! -f "$ROOT/secrets.env" ]; then
    echo "Error: secrets.env not found at $ROOT" >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$ROOT/secrets.env"

# Validate required vars
: "${R2_ACCOUNT_ID:?R2_ACCOUNT_ID not set in secrets.env}"
: "${R2_BUCKET:?R2_BUCKET not set in secrets.env}"
: "${R2_ACCESS_KEY_ID:?R2_ACCESS_KEY_ID not set in secrets.env}"
: "${R2_SECRET_ACCESS_KEY:?R2_SECRET_ACCESS_KEY not set in secrets.env}"

command -v rclone > /dev/null || {
    echo "Error: rclone not found — install it: curl https://rclone.org/install.sh | sudo bash" >&2
    exit 1
}

# Configure rclone via env vars — no config file needed
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
export RCLONE_CONFIG_R2_ACL=private
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

# Resolve source object
if [ -n "${1:-}" ]; then
    OBJECT="garden-${1}.sqlite3"
else
    OBJECT="garden-latest.sqlite3"
fi

DB_PATH="${DB_PATH:-$ROOT/garden.sqlite3}"
DEST="$DB_PATH"

echo "Downloading r2:${R2_BUCKET}/${OBJECT} → ${DEST} ..."

# Safety: back up any existing DB before overwriting
if [ -f "$DEST" ]; then
    SAFEGUARD="${DEST}.pre-restore-$(date -u +%Y%m%dT%H%M%S)"
    cp "$DEST" "$SAFEGUARD"
    echo "Existing DB saved as: $SAFEGUARD"
fi

rclone copyto "r2:${R2_BUCKET}/${OBJECT}" "$DEST"

echo "Restore complete: $DEST ($(du -sh "$DEST" | cut -f1))"
echo ""
echo "Next steps:"
echo "  sudo systemctl restart garden-agent"
echo "  curl -s localhost:8001/health"
