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
    _in_quiet_hours,
    _already_pruned_today,
    _maybe_prune_retention,
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

    now = _local_now()
    # Store a last_fired_ts anchored to local midnight today, not a fixed
    # "1 hour ago" offset -- near local midnight, "1 hour ago" in UTC can
    # land on the previous local day and make this test flaky.
    ts = now.replace(hour=0, minute=1, second=0, microsecond=0).isoformat()
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

    # Fixed noon-UTC so the 1-hour-ago ts never crosses a day boundary on CI
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    ts = (now - timedelta(hours=1)).isoformat()
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


# ── _in_quiet_hours ────────────────────────────────────────────────────────────

def _set_quiet_hours(monkeypatch, **kwargs):
    from garden.config import cfg as cfg_mod
    monkeypatch.setattr(cfg_mod, "quiet_hours", kwargs, raising=False)


def _set_local_hour(monkeypatch, hour: int):
    from garden.agent import runner
    fixed = datetime(2024, 6, 15, hour, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(runner, "_local_now", lambda: fixed)


def test_quiet_hours_disabled_always_allows(monkeypatch):
    _set_quiet_hours(monkeypatch, enabled=False, start_hour=7, end_hour=20)
    _set_local_hour(monkeypatch, 2)  # deep night
    assert _in_quiet_hours() is False


def test_quiet_hours_night_is_held(monkeypatch):
    _set_quiet_hours(monkeypatch, enabled=True, start_hour=7, end_hour=20)
    _set_local_hour(monkeypatch, 23)
    assert _in_quiet_hours() is True


def test_quiet_hours_day_is_allowed(monkeypatch):
    _set_quiet_hours(monkeypatch, enabled=True, start_hour=7, end_hour=20)
    _set_local_hour(monkeypatch, 12)
    assert _in_quiet_hours() is False


def test_quiet_hours_start_boundary_allowed(monkeypatch):
    _set_quiet_hours(monkeypatch, enabled=True, start_hour=7, end_hour=20)
    _set_local_hour(monkeypatch, 7)
    assert _in_quiet_hours() is False


def test_quiet_hours_end_boundary_held(monkeypatch):
    """end_hour is exclusive: alerts stop right at 20:00."""
    _set_quiet_hours(monkeypatch, enabled=True, start_hour=7, end_hour=20)
    _set_local_hour(monkeypatch, 20)
    assert _in_quiet_hours() is True


def test_quiet_hours_wraps_midnight(monkeypatch):
    """A window like 22-6 spans midnight; 23:00 and 3:00 should be allowed,
    noon should be held."""
    _set_quiet_hours(monkeypatch, enabled=True, start_hour=22, end_hour=6)
    _set_local_hour(monkeypatch, 23)
    assert _in_quiet_hours() is False
    _set_local_hour(monkeypatch, 3)
    assert _in_quiet_hours() is False
    _set_local_hour(monkeypatch, 12)
    assert _in_quiet_hours() is True


# ── retention prune ────────────────────────────────────────────────────────────

def test_already_pruned_today_no_record(monkeypatch):
    from garden import storage
    monkeypatch.setattr(storage, "get_alert_state", lambda rule_id: {"last_fired_ts": ""})
    assert _already_pruned_today() is False


def test_already_pruned_today_ran_today(monkeypatch):
    from garden import storage
    today = datetime.now(timezone.utc).date().isoformat()
    monkeypatch.setattr(
        storage, "get_alert_state",
        lambda rule_id: {"last_fired_ts": f"{today}T03:00:00+00:00"},
    )
    assert _already_pruned_today() is True


def test_already_pruned_today_ran_yesterday(monkeypatch):
    from garden import storage
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    monkeypatch.setattr(
        storage, "get_alert_state",
        lambda rule_id: {"last_fired_ts": f"{yesterday}T03:00:00+00:00"},
    )
    assert _already_pruned_today() is False


def test_maybe_prune_retention_disabled_skips(monkeypatch):
    from garden.config import cfg as cfg_mod
    from garden import storage

    monkeypatch.setattr(cfg_mod, "retention", {"enabled": False}, raising=False)
    called = []
    monkeypatch.setattr(storage, "prune_old_data", lambda cutoff: called.append(cutoff) or (0, 0))
    _maybe_prune_retention()
    assert called == []


def test_maybe_prune_retention_already_ran_today_skips(monkeypatch):
    from garden.config import cfg as cfg_mod
    from garden import storage

    monkeypatch.setattr(cfg_mod, "retention", {"enabled": True, "days": 30}, raising=False)
    today = datetime.now(timezone.utc).date().isoformat()
    monkeypatch.setattr(
        storage, "get_alert_state",
        lambda rule_id: {"last_fired_ts": f"{today}T03:00:00+00:00"},
    )
    called = []
    monkeypatch.setattr(storage, "prune_old_data", lambda cutoff: called.append(cutoff) or (0, 0))
    _maybe_prune_retention()
    assert called == []


def test_maybe_prune_retention_runs_and_records_state(monkeypatch):
    from garden.config import cfg as cfg_mod
    from garden import storage

    monkeypatch.setattr(cfg_mod, "retention", {"enabled": True, "days": 30, "vacuum": True}, raising=False)
    monkeypatch.setattr(storage, "get_alert_state", lambda rule_id: {"last_fired_ts": ""})

    prune_calls = []
    monkeypatch.setattr(storage, "prune_old_data", lambda cutoff: prune_calls.append(cutoff) or (5, 2))
    vacuum_calls = []
    monkeypatch.setattr(storage, "vacuum", lambda: vacuum_calls.append(True))
    state_calls = []
    monkeypatch.setattr(
        storage, "set_alert_state",
        lambda rule_id, sensor_key, active, last_fired_ts: state_calls.append((rule_id, active, last_fired_ts)),
    )

    _maybe_prune_retention()

    assert len(prune_calls) == 1
    assert vacuum_calls == [True]  # rows were deleted -> vacuum runs
    assert state_calls[0][0] == "retention_prune"


def test_maybe_prune_retention_skips_vacuum_when_nothing_deleted(monkeypatch):
    from garden.config import cfg as cfg_mod
    from garden import storage

    monkeypatch.setattr(cfg_mod, "retention", {"enabled": True, "days": 30, "vacuum": True}, raising=False)
    monkeypatch.setattr(storage, "get_alert_state", lambda rule_id: {"last_fired_ts": ""})
    monkeypatch.setattr(storage, "prune_old_data", lambda cutoff: (0, 0))
    vacuum_calls = []
    monkeypatch.setattr(storage, "vacuum", lambda: vacuum_calls.append(True))
    monkeypatch.setattr(
        storage, "set_alert_state",
        lambda rule_id, sensor_key, active, last_fired_ts: None,
    )

    _maybe_prune_retention()

    assert vacuum_calls == []
