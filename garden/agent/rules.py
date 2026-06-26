"""
rules.py — Pure deterministic threshold checks.

Each check function returns a list of RuleResult objects — one per sensor key
that tripped. They read only from storage + config; no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from garden import storage
from garden.config import cfg


@dataclass
class RuleResult:
    rule_id: str        # e.g. "soil_moisture_low:soilmoisture1"
    sensor_key: str
    fired: bool
    title: str          # short Telegram title
    body: str           # detail line (used as fallback if LLM unavailable)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _now_utc().isoformat()


# ── soil moisture low ─────────────────────────────────────────────────────────

def check_soil_moisture_low() -> list[RuleResult]:
    t = cfg.thresholds.get("soil_moisture_low", {})
    keys = t.get("sensor_keys", [])
    threshold = t.get("below", 30)
    consecutive = t.get("consecutive", 3)

    results = []
    for key in keys:
        label = cfg.sensor_label(key)
        recent = storage.recent_values(key, consecutive)
        fired = len(recent) == consecutive and all(v < threshold for v in recent)
        if fired:
            latest = recent[0]
            body = (
                f"{label} has been below {threshold}% for {consecutive} consecutive readings "
                f"(current: {latest:.0f}%). Time to water."
            )
        else:
            body = ""
        results.append(RuleResult(
            rule_id=f"soil_moisture_low:{key}",
            sensor_key=key,
            fired=fired,
            title=f"Low soil moisture: {label}",
            body=body,
        ))
    return results


# ── soil moisture rapid drop ──────────────────────────────────────────────────

def check_soil_moisture_rapid_drop() -> list[RuleResult]:
    t = cfg.thresholds.get("soil_moisture_rapid_drop", {})
    keys = t.get("sensor_keys", [])
    drop_pct = t.get("drop_pct", 15)
    window_minutes = t.get("window_minutes", 60)

    results = []
    for key in keys:
        label = cfg.sensor_label(key)
        rows = storage.series(key, hours=2)  # fetch 2h, filter below

        cutoff = _now_utc().timestamp() - window_minutes * 60
        window_rows = [
            r for r in rows
            if datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp() >= cutoff
        ]

        if len(window_rows) < 2:
            results.append(RuleResult(
                rule_id=f"soil_moisture_rapid_drop:{key}",
                sensor_key=key,
                fired=False,
                title=f"Rapid moisture drop: {label}",
                body="",
            ))
            continue

        oldest_val = window_rows[0]["value"]
        newest_val = window_rows[-1]["value"]
        drop = oldest_val - newest_val
        fired = drop >= drop_pct

        body = (
            f"{label} dropped {drop:.1f}% in {window_minutes} min "
            f"({oldest_val:.0f}% → {newest_val:.0f}%). Check for leak or drainage issue."
        ) if fired else ""

        results.append(RuleResult(
            rule_id=f"soil_moisture_rapid_drop:{key}",
            sensor_key=key,
            fired=fired,
            title=f"Rapid moisture drop: {label}",
            body=body,
        ))
    return results


# ── battery low ───────────────────────────────────────────────────────────────

def check_battery_low() -> list[RuleResult]:
    t = cfg.thresholds.get("battery_low", {})
    keys = t.get("sensor_keys", [])
    threshold = t.get("below", 1.1)

    results = []
    for key in keys:
        label = cfg.sensor_label(key)
        recent = storage.recent_values(key, 1)
        fired = bool(recent) and recent[0] < threshold
        body = (
            f"{label} is at {recent[0]:.2f}V (below {threshold}V). Replace battery soon."
        ) if fired else ""
        results.append(RuleResult(
            rule_id=f"battery_low:{key}",
            sensor_key=key,
            fired=fired,
            title=f"Low battery: {label}",
            body=body,
        ))
    return results


# ── temperature frost ─────────────────────────────────────────────────────────

def check_temp_frost() -> list[RuleResult]:
    t = cfg.thresholds.get("temp_frost", {})
    keys = t.get("sensor_keys", [])
    threshold = t.get("below", 2.0)

    results = []
    for key in keys:
        label = cfg.sensor_label(key)
        recent = storage.recent_values(key, 1)
        fired = bool(recent) and recent[0] < threshold
        body = (
            f"{label} is {recent[0] * 9 / 5 + 32:.1f}°F. Frost risk. Consider covering plants."
        ) if fired else ""
        results.append(RuleResult(
            rule_id=f"temp_frost:{key}",
            sensor_key=key,
            fired=fired,
            title=f"Frost warning: {label}",
            body=body,
        ))
    return results


# ── temperature heat ──────────────────────────────────────────────────────────

def check_temp_heat() -> list[RuleResult]:
    t = cfg.thresholds.get("temp_heat", {})
    keys = t.get("sensor_keys", [])
    threshold = t.get("above", 38.0)

    results = []
    for key in keys:
        label = cfg.sensor_label(key)
        recent = storage.recent_values(key, 1)
        fired = bool(recent) and recent[0] > threshold
        body = (
            f"{label} is {recent[0] * 9 / 5 + 32:.1f}°F. Heat stress risk. Consider shade or watering."
        ) if fired else ""
        results.append(RuleResult(
            rule_id=f"temp_heat:{key}",
            sensor_key=key,
            fired=fired,
            title=f"Heat warning: {label}",
            body=body,
        ))
    return results


# ── dead-sensor watchdog ──────────────────────────────────────────────────────

def check_watchdog() -> list[RuleResult]:
    w = cfg.watchdog
    sensor_timeout = w.get("sensor_timeout_minutes", 30)
    now = _now_utc().timestamp()

    results = []
    known_keys = [k for k in cfg.sensors]
    for key in known_keys:
        ts = storage.last_seen(key)
        if ts is None:
            continue  # never seen — don't alert on sensors not yet wired
        last = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        minutes_silent = (now - last) / 60
        fired = minutes_silent >= sensor_timeout
        label = cfg.sensor_label(key)
        body = (
            f"{label} has not reported in {minutes_silent:.0f} min. "
            f"Check gateway and sensor connection."
        ) if fired else ""
        results.append(RuleResult(
            rule_id=f"watchdog:{key}",
            sensor_key=key,
            fired=fired,
            title=f"Sensor silent: {label}",
            body=body,
        ))
    return results


# ── gateway watchdog (no POST at all) ────────────────────────────────────────

def check_gateway_watchdog() -> RuleResult:
    timeout = cfg.watchdog.get("timeout_minutes", 20)
    info = storage.health_info()
    last_ts = info.get("last_reading_ts")

    if last_ts is None:
        return RuleResult(
            rule_id="watchdog:gateway",
            sensor_key="",
            fired=False,
            title="Gateway silent",
            body="",
        )

    now = _now_utc().timestamp()
    last = datetime.fromisoformat(last_ts.replace("Z", "+00:00")).timestamp()
    minutes_silent = (now - last) / 60
    fired = minutes_silent >= timeout
    body = (
        f"No data from gateway in {minutes_silent:.0f} min. "
        f"Check GW1200 power and network."
    ) if fired else ""
    return RuleResult(
        rule_id="watchdog:gateway",
        sensor_key="",
        fired=fired,
        title="Gateway silent",
        body=body,
    )


# ── run all instant rules (called on every POST) ──────────────────────────────

def run_instant() -> list[RuleResult]:
    results: list[RuleResult] = []
    results.extend(check_soil_moisture_low())
    results.extend(check_soil_moisture_rapid_drop())
    results.extend(check_battery_low())
    results.extend(check_temp_frost())
    results.extend(check_temp_heat())
    return results


# ── run all cron rules (called every 15 min) ──────────────────────────────────

def run_cron() -> list[RuleResult]:
    results: list[RuleResult] = []
    results.extend(check_watchdog())
    results.append(check_gateway_watchdog())
    return results
