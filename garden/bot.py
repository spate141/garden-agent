"""
bot.py — Inbound Telegram bot commands (/bed1, /beds, /weather, /air, /brief, /help).

Counterpart to telegram.py (outbound-only). Telegram delivers each command as a
webhook POST to /api/telegram (see garden/main.py), which calls handle_update()
here. Replies reuse existing storage/derived/weather helpers — no new data paths.

Public API:
  handle_update(update)   — parse a Telegram Update dict, dispatch, reply.
  dispatch(command)       — command string (no leading '/') → reply text (HTML).
  command_menu()          — [{"command", "description"}, ...] for setMyCommands.
  setup(base_url, secret) — register the webhook + command menu (idempotent).

Security: handle_update() only acts on messages from cfg.telegram_chat_id — the
webhook route itself is what checks Telegram's secret-token header (main.py).

CLI: `python -m garden.bot --setup` registers the webhook + menu; run manually
or via deploy.sh whenever TELEGRAM_WEBHOOK_SECRET / GARDEN_PUBLIC_URL are set.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from garden import derived as drv
from garden import storage
from garden import telegram
from garden.config import cfg

log = logging.getLogger("garden.bot")

_STATUS_WORD = {
    "ok": "Good", "dry": "Dry", "wet": "Wet",
    "cold": "Cold", "heat": "Heat stress", "unknown": "Unknown",
}

_STATIC_COMMANDS: list[tuple[str, str]] = [
    ("beds", "Summary of all beds"),
    ("weather", "Current conditions + today's forecast"),
    ("air", "VPD, dew point, feels-like"),
    ("brief", "Send the morning brief now"),
    ("help", "List all commands"),
]


# ── shared lookups ────────────────────────────────────────────────────────────

def _latest_map() -> dict[str, dict[str, Any]]:
    return {r["sensor_key"]: r for r in storage.latest()}


def _air_temp_f(latest: dict[str, dict[str, Any]]) -> float | None:
    src_key = cfg.derived.get("source", {}).get("temp", "temp1_f")
    row = latest.get(src_key)
    return row["value"] if row else None


def _minutes_ago(ts: str) -> float:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60


def _bed_commands() -> list[tuple[str, str]]:
    """(command, description) per configured bed, e.g. ('bed4', 'Bed 4 · zucchini, eggplant')."""
    out: list[tuple[str, str]] = []
    for bed in cfg.dashboard.get("beds", []):
        bed_id = bed.get("id", "")
        if not bed_id:
            continue
        name = bed.get("name", bed_id)
        crops = ", ".join(drv.family_labels(bed.get("plants", [])))
        desc = f"{name} · {crops}" if crops else name
        out.append((bed_id, desc[:256]))
    return out


# ── reply builders ────────────────────────────────────────────────────────────

def _bed_summary(bed: dict[str, Any]) -> str:
    name = bed.get("name", bed.get("id", "Bed"))
    sensors = bed.get("sensors", {})
    moist_key = sensors.get("soil_moisture")
    batt_key = sensors.get("soil_battery")
    latest = _latest_map()

    moist_row = latest.get(moist_key) if moist_key else None
    lines = [f"<b>{name}</b>"]

    if moist_row is None:
        lines.append("No moisture data yet.")
        return "\n".join(lines)

    moist = moist_row["value"]
    air_temp_f = _air_temp_f(latest)
    if air_temp_f is not None:
        stress = drv.bed_stress(bed.get("plants", []), moist, air_temp_f, cfg.crops)
        lines.append(f"Moisture: {moist:.0f}%")
        lines.append(stress["reason"])
    else:
        lines.append(f"Moisture: {moist:.0f}%")
        crops = ", ".join(drv.family_labels(bed.get("plants", [])))
        if crops:
            lines.append(f"Crops: {crops}")

    batt_row = latest.get(batt_key) if batt_key else None
    if batt_row is not None:
        lines.append(f"Battery: {batt_row['value']:.2f}V")

    lines.append(f"Updated {_minutes_ago(moist_row['ts']):.0f} min ago")
    return "\n".join(lines)


def _all_beds() -> str:
    beds = cfg.dashboard.get("beds", [])
    if not beds:
        return "No beds configured."

    latest = _latest_map()
    air_temp_f = _air_temp_f(latest)

    lines = ["<b>\U0001f331 All beds</b>"]
    for bed in beds:
        name = bed.get("name", bed.get("id", "Bed"))
        moist_key = bed.get("sensors", {}).get("soil_moisture")
        moist_row = latest.get(moist_key) if moist_key else None
        crops = ", ".join(drv.family_labels(bed.get("plants", [])))
        label = f"{name} ({crops})" if crops else name

        if moist_row is None:
            lines.append(f"{label}: no data")
            continue

        moist = moist_row["value"]
        if air_temp_f is not None:
            stress = drv.bed_stress(bed.get("plants", []), moist, air_temp_f, cfg.crops)
            status = _STATUS_WORD.get(stress["status"], "?")
            lines.append(f"{label}: {moist:.0f}% · {status}")
        else:
            lines.append(f"{label}: {moist:.0f}%")

    return "\n".join(lines)


def _weather() -> str:
    from garden.agent.weather import forecast_summary, get_current, get_forecast

    fc = get_forecast()
    lines = ["<b>☀️ Weather</b>", forecast_summary(fc)]

    cur = get_current()
    if cur:
        rain_note = f", raining ({cur['intensity']})" if cur.get("is_raining") else ""
        lines.append(
            f"Right now: {cur['conditions']}, cloud cover {cur.get('cloud_cover_pct', '?')}%{rain_note}"
        )
    return "\n".join(lines)


def _air() -> str:
    latest = _latest_map()
    lines = ["<b>\U0001f32c️ Air</b>"]

    vpd_row = latest.get("vpd_kpa")
    if vpd_row:
        _status, label = drv.vpd_status(vpd_row["value"], cfg.derived.get("thresholds", {}))
        lines.append(f"VPD: {vpd_row['value']:.2f} kPa ({label})")

    dp_row = latest.get("dewpoint_f")
    if dp_row:
        is_frost, frost_msg = drv.frost_risk(
            dp_row["value"], cfg.derived.get("frost_dewpoint_f", 35.6)
        )
        lines.append(frost_msg if is_frost else f"Dew point: {dp_row['value']:.1f}°F")

    hi_row = latest.get("heatindex_f")
    if hi_row:
        lines.append(f"Feels like: {hi_row['value']:.1f}°F")

    if len(lines) == 1:
        lines.append("No air data available yet.")
    return "\n".join(lines)


def _help() -> str:
    lines = ["<b>Available commands</b>"]
    for cmd, desc in _bed_commands() + _STATIC_COMMANDS:
        lines.append(f"/{cmd} — {desc}")
    return "\n".join(lines)


# ── dispatch ───────────────────────────────────────────────────────────────────

def dispatch(command: str) -> str:
    """Command string (no leading '/', already lowercased) → reply text (Telegram HTML)."""
    bed_by_id = {bed.get("id"): bed for bed in cfg.dashboard.get("beds", []) if bed.get("id")}
    if command in bed_by_id:
        return _bed_summary(bed_by_id[command])
    if command == "beds":
        return _all_beds()
    if command == "weather":
        return _weather()
    if command == "air":
        return _air()
    if command == "brief":
        from garden.agent.runner import send_daily_brief
        send_daily_brief(force=True)
        return "Morning brief sent."
    return _help()


def handle_update(update: dict[str, Any]) -> None:
    """
    Parse a Telegram Update dict and reply. Silently drops anything that isn't
    a text message from the owner's chat_id — this is the second layer of
    defense after the webhook route's secret-token check (main.py).
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = str(message.get("chat", {}).get("id", ""))
    if not chat_id or chat_id != str(cfg.telegram_chat_id):
        log.warning("Ignoring Telegram update from unauthorized chat_id=%s", chat_id)
        return

    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return

    # "/bed4@garden_bot arg1 arg2" → "bed4"
    command = text[1:].split()[0].split("@")[0].lower()

    try:
        reply = dispatch(command)
    except Exception:
        log.exception("Command handling failed for /%s", command)
        reply = "Something went wrong handling that command."

    telegram.send_message(reply, html=True)


# ── menu + webhook registration ───────────────────────────────────────────────

def command_menu() -> list[dict[str, str]]:
    """[{"command", "description"}, ...] for Telegram's setMyCommands, in menu order."""
    return [
        {"command": cmd, "description": desc}
        for cmd, desc in _bed_commands() + _STATIC_COMMANDS
    ]


def setup(base_url: str | None = None, secret: str | None = None) -> None:
    """
    Register the webhook URL + command menu with Telegram. Idempotent — safe to
    call on every deploy. No-ops with a warning if base_url/secret aren't set.
    """
    base_url = base_url or cfg.public_base_url
    secret = secret or cfg.telegram_webhook_secret
    if not base_url or not secret:
        log.warning(
            "garden.bot.setup: GARDEN_PUBLIC_URL / TELEGRAM_WEBHOOK_SECRET not set — skipping"
        )
        return

    api = f"https://api.telegram.org/bot{cfg.telegram_token}"

    webhook_url = f"{base_url}/api/telegram"
    r = httpx.post(
        f"{api}/setWebhook",
        json={"url": webhook_url, "secret_token": secret},
        timeout=10,
    )
    r.raise_for_status()
    log.info("Webhook set: %s -> %s", webhook_url, r.json())

    menu = command_menu()
    r = httpx.post(f"{api}/setMyCommands", json={"commands": menu}, timeout=10)
    r.raise_for_status()
    log.info("Command menu set (%d commands): %s", len(menu), r.json())


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="garden-agent Telegram bot setup")
    parser.add_argument(
        "--setup", action="store_true", help="Register webhook + command menu with Telegram"
    )
    args = parser.parse_args()

    if args.setup:
        setup()
