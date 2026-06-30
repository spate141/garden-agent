"""
runner.py — Evaluate rules, apply cooldowns, dispatch Telegram alerts.

Two entry points:
  evaluate_instant(snap_id, ts, metrics) — called inline on every POST
  run_cron_tick()                        — called by the systemd timer every 15 min

The cron tick also handles the daily morning brief (replaces the old heartbeat).
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from garden import storage
from garden.agent import llm
from garden.agent.rules import RuleResult, run_cron, run_instant
from garden.agent.weather import forecast_summary, get_forecast
from garden.config import cfg
from garden.telegram import tg

log = logging.getLogger("garden.runner")

# Maps rule_id prefix → cooldown key in config.yaml cooldowns dict
_COOLDOWN_KEY: dict[str, str] = {
    "soil_moisture_low":          "soil_moisture_low_minutes",
    "soil_moisture_rapid_drop":   "soil_moisture_rapid_drop_minutes",
    "soil_moisture_rapid_rise":   "soil_moisture_rapid_rise_minutes",
    "battery_low":                "battery_low_minutes",
    "temp_frost":                 "temp_frost_minutes",
    "temp_heat":                  "temp_heat_minutes",
    "watchdog":                   "watchdog_minutes",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cooldown_minutes(rule_id: str) -> int:
    prefix = rule_id.split(":")[0]
    key = _COOLDOWN_KEY.get(prefix, "watchdog_minutes")
    return cfg.cooldowns.get(key, 60)


def _in_cooldown(state: dict, rule_id: str) -> bool:
    """True if the rule fired recently and is still within its cooldown window."""
    last_fired = state.get("last_fired_ts", "")
    if not last_fired:
        return False
    cooldown = _cooldown_minutes(rule_id)
    try:
        last_dt = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        return elapsed < cooldown
    except ValueError:
        return False


def _dispatch(result: RuleResult) -> None:
    """Fire the alert and update alert_state."""
    now = _now_iso()
    log.info("Alert firing: %s — %s", result.rule_id, result.title)
    if result.rule_id.startswith("watchdog:"):
        body = result.body
    else:
        body = llm.write_alert(result.rule_id, result.sensor_key, result.title, result.body)
    tg(result.title, body)
    storage.set_alert_state(result.rule_id, result.sensor_key, active=True, last_fired_ts=now)


def _evaluate(results: list[RuleResult]) -> None:
    for result in results:
        state = storage.get_alert_state(result.rule_id)
        was_active = bool(state.get("active", 0))

        if result.fired:
            if was_active and _in_cooldown(state, result.rule_id):
                log.debug("Suppressed (cooldown): %s", result.rule_id)
                continue
            _dispatch(result)
        else:
            if was_active:
                log.info("Condition cleared: %s", result.rule_id)
                storage.set_alert_state(result.rule_id, result.sensor_key, active=False)


def evaluate_instant(snap_id: int, ts: str, metrics: dict) -> None:
    """Run instant rules after a POST. Called from main.py."""
    try:
        results = run_instant()
        _evaluate(results)
    except Exception:
        log.exception("evaluate_instant failed for snapshot %d", snap_id)


def run_cron_tick() -> None:
    """Run cron rules + daily brief. Called by the systemd timer every 15 min."""
    log.info("Cron tick starting")
    try:
        results = run_cron()
        _evaluate(results)
    except Exception:
        log.exception("Cron rule evaluation failed")

    try:
        _maybe_daily_brief()
    except Exception:
        log.exception("Daily brief failed")

    log.info("Cron tick complete")


# ── Daily morning brief ───────────────────────────────────────────────────────

_BRIEF_RULE_ID = "daily_brief"


def _sensor_summary() -> str:
    """
    Build a compact sensor summary for the LLM from the latest readings.
    Includes raw readings, derived agronomic metrics, and per-bed stress assessment.
    """
    rows = storage.latest()
    if not rows:
        return "No sensor data available yet."

    PRIORITY = ["soilmoisture1", "soilmoisture2", "temp_f", "humidity", "soilbatt1", "soilbatt2"]
    DERIVED   = ["vpd_kpa", "dewpoint_f", "heatindex_f"]
    by_key    = {r["sensor_key"]: r for r in rows}

    lines: list[str] = []

    # Priority raw readings
    for key in PRIORITY:
        if key in by_key:
            r = by_key[key]
            lines.append(f"  {cfg.sensor_label(key)}: {r['value']:.1f}{r['unit']}")

    # Derived agronomic metrics (high interpretive value for the LLM)
    derived_lines: list[str] = []
    for key in DERIVED:
        if key in by_key:
            r = by_key[key]
            derived_lines.append(f"  {cfg.sensor_label(key)}: {r['value']:.2f}{r['unit']}")
    if derived_lines:
        lines.append("  --- derived ---")
        lines.extend(derived_lines)

    # Per-bed stress assessment
    try:
        from garden import derived as drv
        src_temp_key = cfg.derived.get("source", {}).get("temp", "temp1_f")
        air_temp_row = by_key.get(src_temp_key)
        air_temp_f   = air_temp_row["value"] if air_temp_row else None
        if air_temp_f is not None:
            lines.append("  --- bed stress ---")
            for bed in cfg.dashboard.get("beds", []):
                moist_key = bed.get("sensors", {}).get("soil_moisture")
                moist_row = by_key.get(moist_key) if moist_key else None
                soil_moist = moist_row["value"] if moist_row else None
                if soil_moist is not None:
                    stress = drv.bed_stress(
                        bed.get("plants", []), soil_moist, air_temp_f, cfg.crops
                    )
                    lines.append(f"  {bed.get('name', bed.get('id'))}: {stress['reason']}")
    except Exception:
        log.debug("Bed stress assessment skipped", exc_info=True)

    # Remaining sensors (not in priority or derived lists)
    skip = set(PRIORITY) | set(DERIVED)
    for key, r in by_key.items():
        if key not in skip:
            lines.append(f"  {cfg.sensor_label(key)}: {r['value']:.1f}{r['unit']}")

    return "\n".join(lines) if lines else "No sensor data available."


def _local_now() -> datetime:
    """Current time in the configured local timezone."""
    tz_name = cfg.location.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.warning("Unknown timezone %r, falling back to UTC", tz_name)
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


def _already_sent_today(local_now: datetime) -> bool:
    """True if the brief was already sent today (local date)."""
    state = storage.get_alert_state(_BRIEF_RULE_ID)
    last_fired = state.get("last_fired_ts", "")
    if not last_fired:
        return False
    try:
        last_dt = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
        tz_name = cfg.location.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            log.warning("Unknown timezone %r in _already_sent_today, falling back to UTC", tz_name)
            tz = ZoneInfo("UTC")
        last_local = last_dt.astimezone(tz)
        return last_local.date() == local_now.astimezone(tz).date()
    except Exception:
        return False


def send_daily_brief(force: bool = False) -> None:
    """
    Send the morning garden brief. Called by run_cron_tick() and the --brief CLI flag.

    Args:
        force: if True, skip the hour check and dedup (for testing / manual send).
    """
    if not cfg.daily_brief.get("enabled", True):
        log.info("Daily brief disabled in config")
        return

    local_now = _local_now()

    if not force:
        hour_local = cfg.daily_brief.get("hour_local", 7)
        if local_now.hour != hour_local:
            return
        if _already_sent_today(local_now):
            log.debug("Daily brief already sent today, skipping")
            return

    log.info("Sending daily brief (force=%s, local time=%s)", force, local_now.strftime("%H:%M %Z"))

    fc = get_forecast()
    sensor_sum = _sensor_summary()
    body = llm.write_daily_brief(fc, sensor_sum)

    weather_line = forecast_summary(fc)
    title = f"Morning Brief · {local_now.strftime('%a %b %-d')}"

    tg(title, body, html=True)
    storage.set_alert_state(_BRIEF_RULE_ID, "", active=False, last_fired_ts=_now_iso())
    log.info("Daily brief sent")


def _maybe_daily_brief() -> None:
    send_daily_brief(force=False)


# ── CLI entry point (used by garden-cron.service) ────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="garden-agent cron runner")
    parser.add_argument("--cron",  action="store_true", help="Run cron tick (rules + brief)")
    parser.add_argument("--brief", action="store_true", help="Force-send morning brief now (ignores hour/dedup)")
    args = parser.parse_args()

    if args.brief:
        storage.init_db()
        send_daily_brief(force=True)
    elif args.cron:
        run_cron_tick()
