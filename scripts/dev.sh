#!/usr/bin/env bash
# dev.sh — One-command local dev loop: start the server (dry-run alerts) and
# stream simulated sensor data into it so the dashboard has live data.
#
# Usage:
#   ./scripts/dev.sh [simulate.py args, e.g. --interval 5]
#
# Ctrl-C stops both the simulator and the server.

set -euo pipefail

cd "$(dirname "$0")/.."

export GARDEN_DRY_RUN=1

echo "→ Starting server (GARDEN_DRY_RUN=1) ..."
uv run uvicorn garden.main:app --reload --port 8000 &
SERVER_PID=$!

cleanup() {
    echo ""
    echo "→ Stopping server (pid $SERVER_PID) ..."
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "→ Waiting for server to become healthy ..."
for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
        echo "✓ Server is up."
        break
    fi
    sleep 1
done

uv run python scripts/simulate.py --host http://127.0.0.1:8000 "$@"
