#!/usr/bin/env bash
# Nightly SQLite backup — keeps last 7 days, uses SQLite .backup for a
# consistent hot copy (safe while the app is running with WAL mode).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

# Load DB_PATH from secrets.env if not already set
if [[ -z "${DB_PATH:-}" ]]; then
    source "$ROOT/secrets.env"
fi

DB_PATH="${DB_PATH:-$ROOT/garden.sqlite3}"
BACKUP_DIR="$ROOT/backups"
DATE=$(date -u +%Y%m%d)
BACKUP_FILE="$BACKUP_DIR/garden-${DATE}.sqlite3"

mkdir -p "$BACKUP_DIR"

sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup written: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"

# Prune backups older than 7 days
find "$BACKUP_DIR" -name "garden-*.sqlite3" -mtime +7 -delete
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) old backups pruned (kept last 7 days)"
