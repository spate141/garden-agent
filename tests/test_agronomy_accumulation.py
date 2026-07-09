"""
test_agronomy_accumulation.py — integration tests for
garden.agent.runner.run_daily_agronomy_accumulation(): the same-day rerun
idempotency fix (Fix 1) and the planted_on GDD backfill fix (Fix 2).

storage._conn() opens a fresh sqlite3.connect() per call; with DB_PATH set
to ":memory:" (the conftest.py default), every connection is a distinct,
empty database. These tests need writes and reads to share one database, so
the `db` fixture below points storage at a real on-disk temp file instead,
same technique as tests/test_storage.py.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from garden import storage
from garden.agent import runner
from garden.config import cfg


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_db_path", tmp_path / "test.sqlite3")
    storage.init_db()
    return storage


@pytest.fixture
def one_bed_config():
    """Replace the configured beds with a single controlled test bed."""
    original_beds = cfg.dashboard.get("beds")
    original_agronomy = dict(cfg.agronomy)

    cfg.dashboard["beds"] = [{
        "id": "testbed",
        "name": "Test Bed",
        "sensors": {},
        "plants": ["tomato"],
        "planted_on": runner._local_now().date().isoformat(),
    }]
    cfg.agronomy.clear()
    cfg.agronomy.update({
        "enabled": True,
        "accumulation_hour_local": 23,
        "gdd_temp_key": "temp_f",
        "beds": {},
        "gdd_base_overrides": {},
        "kc_overrides": {},
    })

    yield

    cfg.dashboard["beds"] = original_beds
    cfg.agronomy.clear()
    cfg.agronomy.update(original_agronomy)


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _write_recent_temps(db, tmin: float, tmax: float) -> None:
    """Two temp_f readings within the last few hours (for storage.stats() trailing-24h)."""
    db.write_snapshot(_iso(180), {"temp_f": (tmin, "F")}, {"raw": True})
    db.write_snapshot(_iso(30), {"temp_f": (tmax, "F")}, {"raw": True})


def _write_local_day_temps(db, local_day, tmin: float, tmax: float, tz_name: str) -> None:
    """Two temp_f readings solidly inside one local calendar day (6am/3pm local)."""
    tz = ZoneInfo(tz_name)
    for hour, val in ((6, tmin), (15, tmax)):
        local_dt = datetime(local_day.year, local_day.month, local_day.day, hour, tzinfo=tz)
        db.write_snapshot(local_dt.astimezone(timezone.utc).isoformat(), {"temp_f": (val, "F")}, {"raw": True})


class TestRerunIdempotency:
    def test_forced_rerun_does_not_double_count(self, db, one_bed_config, monkeypatch):
        _write_recent_temps(db, 60.0, 90.0)
        monkeypatch.setattr(runner, "get_forecast", lambda: None)

        runner.run_daily_agronomy_accumulation(force=True)
        first = db.get_bed_agronomy_latest("testbed")
        assert first is not None

        runner.run_daily_agronomy_accumulation(force=True)
        second = db.get_bed_agronomy_latest("testbed")

        assert first["gdd_cumulative"] == second["gdd_cumulative"]
        assert first["water_balance_cumulative"] == second["water_balance_cumulative"]

    def test_repeated_reruns_still_match_a_fresh_sum(self, db, one_bed_config, monkeypatch):
        _write_recent_temps(db, 60.0, 90.0)
        monkeypatch.setattr(runner, "get_forecast", lambda: None)

        for _ in range(3):
            runner.run_daily_agronomy_accumulation(force=True)

        today_str = runner._local_now().date().isoformat()
        row = db.get_bed_agronomy_latest("testbed")
        # (90+60)/2 - 50 = 25.0 GDD for the one day on record, regardless of
        # how many times it was (re)computed.
        assert row["gdd_daily"] == pytest.approx(25.0)
        assert row["gdd_cumulative"] == pytest.approx(25.0)
        assert db.bed_gdd_cumulative_before("testbed", today_str) == 0.0


class TestPlantedOnBackfill:
    def test_backfills_from_planted_on_on_first_run(self, db, one_bed_config, monkeypatch):
        tz_name = cfg.location.get("timezone", "UTC")
        today_local = runner._local_now().date()
        planted = today_local - timedelta(days=5)
        cfg.dashboard["beds"][0]["planted_on"] = planted.isoformat()

        # 5 backfilled days (planted..today-1), each 60/90 -> 25.0 GDD.
        for d in range(5):
            day = planted + timedelta(days=d)
            _write_local_day_temps(db, day, 60.0, 90.0, tz_name)
        # Plus a reading in the last 24h so today's own row can be computed too.
        _write_recent_temps(db, 60.0, 90.0)

        monkeypatch.setattr(runner, "get_forecast", lambda: None)
        runner.run_daily_agronomy_accumulation(force=True)

        series = db.bed_agronomy_series("testbed", days=30)
        assert [r["local_date"] for r in series] == [
            (planted + timedelta(days=d)).isoformat() for d in range(5)
        ] + [today_local.isoformat()]

        # 5 backfilled days + today, each contributing 25.0 GDD.
        latest = db.get_bed_agronomy_latest("testbed")
        assert latest["gdd_cumulative"] == pytest.approx(25.0 * 6)

    def test_no_backfill_when_planted_on_is_today(self, db, one_bed_config, monkeypatch):
        # one_bed_config's default planted_on is today -- nothing to backfill.
        _write_recent_temps(db, 60.0, 90.0)
        monkeypatch.setattr(runner, "get_forecast", lambda: None)

        runner.run_daily_agronomy_accumulation(force=True)

        series = db.bed_agronomy_series("testbed", days=30)
        assert len(series) == 1

    def test_missing_sensor_history_skipped_not_crashed(self, db, one_bed_config, monkeypatch):
        # planted_on is 5 days ago, but there's NO sensor history for any of
        # those days (station wasn't recording yet) -- backfill should skip
        # them silently rather than raise, and today's own row still writes.
        today_local = runner._local_now().date()
        planted = today_local - timedelta(days=5)
        cfg.dashboard["beds"][0]["planted_on"] = planted.isoformat()
        _write_recent_temps(db, 60.0, 90.0)

        monkeypatch.setattr(runner, "get_forecast", lambda: None)
        runner.run_daily_agronomy_accumulation(force=True)

        series = db.bed_agronomy_series("testbed", days=30)
        assert [r["local_date"] for r in series] == [today_local.isoformat()]
