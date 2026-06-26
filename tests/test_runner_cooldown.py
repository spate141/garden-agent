"""
test_runner_cooldown.py — unit tests for the cooldown helpers in runner.py.

These are pure-function tests (no DB, no network) that lock in the logic
that caused the brief-dedup bug: a bad timezone must not make _already_sent_today
return False silently.
"""

from datetime import datetime, timezone, timedelta

from garden.agent.runner import (
    _cooldown_minutes,
    _in_cooldown,
    _already_sent_today,
)


# ── _cooldown_minutes ─────────────────────────────────────────────────────────

def test_cooldown_minutes_watchdog():
    assert _cooldown_minutes("watchdog:gateway") == 360


def test_cooldown_minutes_soil():
    assert _cooldown_minutes("soil_moisture_low:soilmoisture1") == 120


def test_cooldown_minutes_battery():
    assert _cooldown_minutes("battery_low:soilbatt1") == 1440


def test_cooldown_minutes_unknown_falls_back_to_watchdog():
    # Unknown prefixes fall back to watchdog_minutes
    assert _cooldown_minutes("some_unknown_rule") == 360


# ── _in_cooldown ──────────────────────────────────────────────────────────────

def _ts(minutes_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


def test_in_cooldown_recent():
    state = {"last_fired_ts": _ts(30)}  # 30 min ago, cooldown=120
    assert _in_cooldown(state, "watchdog:gateway") is True


def test_in_cooldown_expired():
    state = {"last_fired_ts": _ts(400)}  # 400 min ago, cooldown=360
    assert _in_cooldown(state, "watchdog:gateway") is False


def test_in_cooldown_no_state():
    assert _in_cooldown({}, "watchdog:gateway") is False


def test_in_cooldown_empty_ts():
    assert _in_cooldown({"last_fired_ts": ""}, "watchdog:gateway") is False


# ── _already_sent_today ───────────────────────────────────────────────────────

def _local_now():
    from garden.agent.runner import _local_now
    return _local_now()


def test_already_sent_today_same_day(monkeypatch):
    """Brief sent earlier today → True."""
    from garden import storage
    from zoneinfo import ZoneInfo

    now = _local_now()
    # Store a last_fired_ts for today (an hour ago in UTC)
    ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        storage, "get_alert_state",
        lambda rule_id: {"last_fired_ts": ts}
    )
    assert _already_sent_today(now) is True


def test_already_sent_today_yesterday(monkeypatch):
    """Brief sent yesterday → False."""
    from garden import storage

    now = _local_now()
    ts = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat()
    monkeypatch.setattr(
        storage, "get_alert_state",
        lambda rule_id: {"last_fired_ts": ts}
    )
    assert _already_sent_today(now) is False


def test_already_sent_today_no_record(monkeypatch):
    """No record at all → False (should send)."""
    from garden import storage

    monkeypatch.setattr(
        storage, "get_alert_state",
        lambda rule_id: {"last_fired_ts": ""}
    )
    assert _already_sent_today(_local_now()) is False


def test_already_sent_today_bad_timezone(monkeypatch):
    """Bad timezone in cfg must NOT silently return False (regression for brief 4x bug).
    It should fall back to UTC and still correctly deduplicate."""
    from garden import storage
    from garden import config as cfg_mod

    now = _local_now()
    ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        storage, "get_alert_state",
        lambda rule_id: {"last_fired_ts": ts}
    )
    # Simulate an invalid timezone in cfg
    original = cfg_mod.cfg.location.copy()
    cfg_mod.cfg.location["timezone"] = "Invalid/Zone"
    try:
        # With the fix in place, it falls back to UTC and returns True
        # (both ts and now are within the same UTC day for a 1-hour-ago ts)
        result = _already_sent_today(now)
        # Must not return False (which would re-send the brief)
        assert result is True, "Bad timezone caused dedup to fail — brief would send again"
    finally:
        cfg_mod.cfg.location.update(original)
