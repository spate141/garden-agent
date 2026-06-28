#!/usr/bin/env bash
# deploy.sh — run on the VM after every git pull
# Usage: bash deploy.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"
RELOAD_NEEDED=0

echo "==> garden-agent deploy starting (user: $USER)"
echo "    app dir: $APP_DIR"

# ── 0. Pull latest code ───────────────────────────────────────────────────────
echo "==> Pulling latest code..."
cd "$APP_DIR" && git pull

# ── 1. Pre-flight checks ──────────────────────────────────────────────────────
if [ ! -f "$APP_DIR/secrets.env" ]; then
    echo "Error: secrets.env not found at $APP_DIR — services will not start." >&2
    exit 1
fi

# uv installs to ~/.local/bin; non-login shells may not have it on PATH
# shellcheck source=/dev/null
[ -f "$HOME/.local/bin/env" ] && source "$HOME/.local/bin/env"
command -v uv >/dev/null || { echo "Error: uv not found on PATH — run: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }

# ── 2. Python dependencies ────────────────────────────────────────────────────
echo "==> Syncing Python deps..."
cd "$APP_DIR" && uv sync --quiet

# ── 3. Systemd service files (sed placeholder, copy, reload) ─────────────────
for UNIT in garden-agent.service garden-cron.service garden-cron.timer garden-backup.service garden-backup.timer ecowitt-bridge.service; do
    SRC="$APP_DIR/systemd/$UNIT"
    DST="$SYSTEMD_DIR/$UNIT"
    # Substitute YOUR_VM_USER with the actual user running this script
    PATCHED=$(sed "s/YOUR_VM_USER/$USER/g" "$SRC")
    PATCHED_HASH=$(echo "$PATCHED" | md5sum)
    INSTALLED_HASH=$([ -f "$DST" ] && md5sum < "$DST" || echo "missing")
    if [ "$PATCHED_HASH" != "$INSTALLED_HASH" ]; then
        echo "==> Updating $UNIT..."
        echo "$PATCHED" | sudo tee "$DST" > /dev/null
        RELOAD_NEEDED=1
    fi
done

if [ "$RELOAD_NEEDED" -eq 1 ]; then
    echo "==> systemctl daemon-reload"
    sudo systemctl daemon-reload
fi

# ── 4. Restart services ───────────────────────────────────────────────────────
echo "==> Restarting garden-agent..."
sudo systemctl reset-failed garden-agent 2>/dev/null || true
sudo systemctl restart garden-agent

echo "==> Restarting garden-cron.timer..."
sudo systemctl restart garden-cron.timer

echo "==> Enabling garden-backup.timer..."
sudo systemctl enable --now garden-backup.timer

echo "==> Enabling ecowitt-bridge..."
sudo systemctl enable --now ecowitt-bridge

# ── 5. Health check ───────────────────────────────────────────────────────────
echo "==> Waiting for garden-agent to come up..."
for _ in $(seq 1 10); do
    if curl -sf http://localhost:8001/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo ""
HEALTH=$(curl -s http://localhost:8001/health || echo "(no response — service may be down)")
echo "$HEALTH"
echo ""

# ── 6. Status summary ─────────────────────────────────────────────────────────
echo "==> Service status:"
sudo systemctl is-active --quiet garden-agent      && echo "    garden-agent      : active" || echo "    garden-agent      : FAILED"
sudo systemctl is-active --quiet garden-cron.timer    && echo "    garden-cron.timer    : active" || echo "    garden-cron.timer    : FAILED"
sudo systemctl is-active --quiet garden-backup.timer && echo "    garden-backup.timer  : active" || echo "    garden-backup.timer  : FAILED"
sudo systemctl is-active --quiet ecowitt-bridge       && echo "    ecowitt-bridge       : active" || echo "    ecowitt-bridge       : FAILED"

echo ""
echo "==> Deploy complete."
