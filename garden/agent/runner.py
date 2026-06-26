"""
runner.py — Evaluate rules, apply cooldowns, dispatch Telegram alerts.

Two entry points:
  evaluate_instant(snap_id, ts, metrics) — called inline on every POST
  run_cron()                             — called by the systemd timer every 15 min
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from garden import storage
from garden.agent import llm
from garden.agent.rules import RuleResult, run_cron, run_instant
from garden.config import cfg
from garden.telegram import heartbeat, tg

log = logging.getLogger("garden.runner")

# Maps rule_id prefix → cooldown key in config.yaml cooldowns dict
_COOLDOWN_KEY: dict[str, str] = {
    "soil_moisture_low":          "soil_moisture_low_minutes",
    "soil_moisture_rapid_drop":   "soil_moisture_rapid_drop_minutes",
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
                # Condition cleared — reset so it can fire again next time
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
    """Run cron rules + heartbeat. Called by the systemd timer."""
    log.info("Cron tick starting")
    try:
        results = run_cron()
        _evaluate(results)
    except Exception:
        log.exception("Cron rule evaluation failed")

    try:
        _maybe_heartbeat()
    except Exception:
        log.exception("Heartbeat failed")

    log.info("Cron tick complete")


def _maybe_heartbeat() -> None:
    if not cfg.heartbeat.get("enabled", True):
        return
    hour_utc = cfg.heartbeat.get("hour_utc", 12)
    now = datetime.now(timezone.utc)
    if now.hour != hour_utc:
        return

    info = storage.health_info()
    heartbeat(
        sensor_count=info.get("sensors_seen", 0),
        last_ts=info.get("last_reading_ts"),
    )


# ── CLI entry point (used by garden-cron.service) ────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--cron", action="store_true", help="Run cron tick")
    args = parser.parse_args()
    if args.cron:
        run_cron_tick()
