#!/usr/bin/env bash
# Nightly SQLite backup — keeps last 7 days locally, then pushes to Cloudflare R2.
# Uses SQLite .backup for a consistent hot copy (safe with WAL mode).
#
# R2 upload is optional: if rclone is not installed or R2_* vars are unset,
# the script logs "R2 upload skipped" and exits 0. Local backup always runs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

# Always load secrets.env so R2_* vars are available for manual runs too
if [ -f "$ROOT/secrets.env" ]; then
    # shellcheck source=/dev/null
    source "$ROOT/secrets.env"
fi

DB_PATH="${DB_PATH:-$ROOT/garden.sqlite3}"
BACKUP_DIR="$ROOT/backups"
DATE=$(date -u +%Y%m%d)
BACKUP_FILE="$BACKUP_DIR/garden-${DATE}.sqlite3"

# ── Local backup ──────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup written: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"

# Prune local backups older than 7 days
find "$BACKUP_DIR" -name "garden-*.sqlite3" -mtime +7 -delete
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) old backups pruned (kept last 7 days)"

# ── R2 offsite upload (optional) ──────────────────────────────────────────────
if ! command -v rclone > /dev/null 2>&1; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) R2 upload skipped (rclone not installed)"
    exit 0
fi

if [ -z "${R2_ACCESS_KEY_ID:-}" ] || [ -z "${R2_SECRET_ACCESS_KEY:-}" ] || \
   [ -z "${R2_ACCOUNT_ID:-}" ]    || [ -z "${R2_BUCKET:-}" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) R2 upload skipped (R2_* vars not configured in secrets.env)"
    exit 0
fi

# Configure rclone via env vars — no config file needed
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
export RCLONE_CONFIG_R2_ACL=private
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

DATED_OBJECT="$(basename "$BACKUP_FILE")"
LATEST_OBJECT="garden-latest.sqlite3"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) uploading to r2:${R2_BUCKET}/ ..."

if rclone copyto "$BACKUP_FILE" "r2:${R2_BUCKET}/${DATED_OBJECT}" --quiet && \
   rclone copyto "$BACKUP_FILE" "r2:${R2_BUCKET}/${LATEST_OBJECT}" --quiet; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) R2 upload OK: ${DATED_OBJECT} + ${LATEST_OBJECT}"
else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: R2 upload failed — local backup is intact"
fi
