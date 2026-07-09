"""
test_storage.py — Unit tests for garden.storage's stats() aggregate query.

storage._conn() opens a fresh sqlite3.connect() per call. With DB_PATH set to
":memory:" (the default tests/conftest.py sets for hermetic tests), every
connection is a distinct, empty database, so a write in one _conn() block is
invisible to a later one. These tests need writes and reads to share one
database, so the `db` fixture below points storage at a real on-disk temp
file instead of relying on the conftest default.
"""

from datetime import datetime, timedelta, timezone

import pytest

from garden import storage


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_db_path", tmp_path / "test.sqlite3")
    storage.init_db()
    return storage


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _write(db, sensor_key: str, value: float, minutes_ago: float, unit: str = "") -> None:
    ts = _iso(minutes_ago)
    db.write_snapshot(ts, {sensor_key: (value, unit)}, {"raw": True})


class TestStats:
    def test_unknown_sensor_returns_none(self, db):
        assert db.stats("nope") is None

    def test_single_reading(self, db):
        _write(db, "temp_f", 72.0, 10)
        s = db.stats("temp_f", hours=24)
        assert s["min"] == 72.0
        assert s["max"] == 72.0
        assert s["avg"] == 72.0
        assert s["n"] == 1
        assert s["oldest_value"] == 72.0

    def test_multiple_readings(self, db):
        _write(db, "temp_f", 60.0, 180)
        _write(db, "temp_f", 80.0, 90)
        _write(db, "temp_f", 70.0, 5)
        s = db.stats("temp_f", hours=24)
        assert s["min"] == 60.0
        assert s["max"] == 80.0
        assert s["n"] == 3
        assert abs(s["avg"] - 70.0) < 0.001
        # oldest_value/oldest_ts should reflect the earliest reading in the window
        assert s["oldest_value"] == 60.0

    def test_excludes_readings_outside_window(self, db):
        _write(db, "temp_f", 40.0, 60 * 48)  # 48h ago — outside a 24h window
        _write(db, "temp_f", 75.0, 30)
        s = db.stats("temp_f", hours=24)
        assert s["min"] == 75.0
        assert s["max"] == 75.0
        assert s["n"] == 1

    def test_different_sensor_keys_are_independent(self, db):
        _write(db, "temp_f", 72.0, 10)
        _write(db, "humidity", 55.0, 10)
        assert db.stats("temp_f")["max"] == 72.0
        assert db.stats("humidity")["max"] == 55.0

    def test_excludes_readings_outside_sub_day_window(self, db):
        # Regression test: ingest.py writes `ts` via datetime.isoformat(), e.g.
        # "2026-07-05T14:30:00+00:00" ("T" separator, "+00:00" offset). The
        # cutoff must be compared correctly against that format, or a
        # lexicographic mismatch (e.g. SQLite's datetime('now', '-N hours'),
        # which renders "2026-07-05 11:30:00" with a space and no offset)
        # makes any same-day row satisfy `ts >= cutoff` regardless of hour --
        # collapsing a 3h window down to "today", identical to a 24h window.
        _write(db, "temp_f", 60.0, 5 * 60)  # 5h ago -- outside a 3h window
        _write(db, "temp_f", 75.0, 60)      # 1h ago -- inside a 3h window
        s = db.stats("temp_f", hours=3)
        assert s["n"] == 1
        assert s["min"] == 75.0
        assert s["max"] == 75.0


class TestSeries:
    def test_returns_only_readings_in_window(self, db):
        _write(db, "temp_f", 60.0, 5 * 60)  # 5h ago -- outside a 3h window
        _write(db, "temp_f", 75.0, 60)      # 1h ago -- inside a 3h window
        rows = db.series("temp_f", hours=3)
        assert len(rows) == 1
        assert rows[0]["value"] == 75.0

    def test_narrow_window_returns_all_points_unbucketed(self, db):
        # A narrow window's bucket size (>= 60s) is smaller than the spacing
        # between these readings, so each stays in its own bucket -- the
        # bucket-averaging path should not lose or merge distinct readings.
        _write(db, "temp_f", 60.0, 20)
        _write(db, "temp_f", 65.0, 10)
        _write(db, "temp_f", 70.0, 1)
        rows = db.series("temp_f", hours=1)
        assert [r["value"] for r in rows] == [60.0, 65.0, 70.0]

    def test_wide_window_downsamples(self, db):
        # Simulate ~1 reading every 3 minutes across a 24h window (480
        # points) -- far more than the ~350-point target -- and assert the
        # bucket-averaging path shrinks the payload while still spanning the
        # full window.
        for minutes_ago in range(0, 24 * 60, 3):
            _write(db, "temp_f", float(minutes_ago % 100), minutes_ago)
        rows = db.series("temp_f", hours=24)
        assert 0 < len(rows) < 480
        # Endpoints of the window are still represented.
        assert rows[0]["ts"] < rows[-1]["ts"]


class TestBedDailyAgronomy:
    def test_no_rows_returns_none(self, db):
        assert db.get_bed_agronomy_latest("bed1") is None

    def test_upsert_then_read_latest(self, db):
        db.upsert_bed_agronomy(
            "bed1", "2026-07-07",
            tmax_f=90.0, tmin_f=65.0, gdd_daily=27.5, gdd_cumulative=27.5,
            et0_in=0.2, etc_in=0.23, rain_in=0.0, irrigation_est_in=0.0,
            water_balance_daily=-0.23, water_balance_cumulative=-0.23,
            reset_reason="",
        )
        row = db.get_bed_agronomy_latest("bed1")
        assert row["local_date"] == "2026-07-07"
        assert row["gdd_cumulative"] == 27.5
        assert row["water_balance_cumulative"] == -0.23

    def test_latest_picks_most_recent_date(self, db):
        db.upsert_bed_agronomy("bed1", "2026-07-06", gdd_daily=20.0, gdd_cumulative=20.0)
        db.upsert_bed_agronomy("bed1", "2026-07-07", gdd_daily=25.0, gdd_cumulative=45.0)
        row = db.get_bed_agronomy_latest("bed1")
        assert row["local_date"] == "2026-07-07"
        assert row["gdd_cumulative"] == 45.0

    def test_upsert_overwrites_same_day_not_duplicates(self, db):
        db.upsert_bed_agronomy("bed1", "2026-07-07", gdd_daily=10.0, gdd_cumulative=10.0)
        db.upsert_bed_agronomy("bed1", "2026-07-07", gdd_daily=12.0, gdd_cumulative=12.0)
        series = db.bed_agronomy_series("bed1", days=30)
        assert len(series) == 1
        assert series[0]["gdd_cumulative"] == 12.0

    def test_beds_are_independent(self, db):
        db.upsert_bed_agronomy("bed1", "2026-07-07", gdd_cumulative=10.0)
        db.upsert_bed_agronomy("bed2", "2026-07-07", gdd_cumulative=99.0)
        assert db.get_bed_agronomy_latest("bed1")["gdd_cumulative"] == 10.0
        assert db.get_bed_agronomy_latest("bed2")["gdd_cumulative"] == 99.0

    def test_unknown_column_rejected(self, db):
        with pytest.raises(ValueError):
            db.upsert_bed_agronomy("bed1", "2026-07-07", not_a_real_column=1.0)

    def test_series_ordered_oldest_to_newest(self, db):
        db.upsert_bed_agronomy("bed1", "2026-07-05", gdd_daily=5.0)
        db.upsert_bed_agronomy("bed1", "2026-07-07", gdd_daily=7.0)
        db.upsert_bed_agronomy("bed1", "2026-07-06", gdd_daily=6.0)
        series = db.bed_agronomy_series("bed1", days=30)
        assert [r["local_date"] for r in series] == ["2026-07-05", "2026-07-06", "2026-07-07"]


class TestBedGddCumulativeBefore:
    def test_no_rows_returns_zero(self, db):
        assert db.bed_gdd_cumulative_before("bed1", "2026-07-07") == 0.0

    def test_sums_strictly_before_date(self, db):
        db.upsert_bed_agronomy("bed1", "2026-07-05", gdd_daily=10.0)
        db.upsert_bed_agronomy("bed1", "2026-07-06", gdd_daily=15.0)
        db.upsert_bed_agronomy("bed1", "2026-07-07", gdd_daily=20.0)
        # 2026-07-07 itself is excluded -- "before", not "on or before"
        assert db.bed_gdd_cumulative_before("bed1", "2026-07-07") == 25.0

    def test_idempotent_under_reprocessing_same_day(self, db):
        # Reprocessing 2026-07-07 (e.g. a forced rerun) must not change what
        # bed_gdd_cumulative_before("2026-07-07") returns -- it only sums
        # STRICTLY earlier days, so today's own (repeated) row is irrelevant.
        db.upsert_bed_agronomy("bed1", "2026-07-06", gdd_daily=15.0)
        before_first_run = db.bed_gdd_cumulative_before("bed1", "2026-07-07")
        db.upsert_bed_agronomy("bed1", "2026-07-07", gdd_daily=20.0, gdd_cumulative=35.0)
        db.upsert_bed_agronomy("bed1", "2026-07-07", gdd_daily=20.0, gdd_cumulative=35.0)  # rerun
        after_rerun = db.bed_gdd_cumulative_before("bed1", "2026-07-07")
        assert before_first_run == after_rerun == 15.0


class TestBedWaterBalanceCumulativeSinceReset:
    def test_no_rows_returns_zero(self, db):
        assert db.bed_water_balance_cumulative_since_reset("bed1", "2026-07-07") == 0.0

    def test_sums_from_start_of_history_when_no_reset(self, db):
        db.upsert_bed_agronomy("bed1", "2026-07-05", water_balance_daily=-0.1, reset_reason="")
        db.upsert_bed_agronomy("bed1", "2026-07-06", water_balance_daily=-0.2, reset_reason="")
        assert db.bed_water_balance_cumulative_since_reset("bed1", "2026-07-07") == pytest.approx(-0.3)

    def test_sums_only_since_most_recent_reset(self, db):
        db.upsert_bed_agronomy("bed1", "2026-07-04", water_balance_daily=-0.5, reset_reason="")
        db.upsert_bed_agronomy("bed1", "2026-07-05", water_balance_daily=0.3, reset_reason="good_soak")
        db.upsert_bed_agronomy("bed1", "2026-07-06", water_balance_daily=-0.1, reset_reason="")
        # The 07-04 deficit is behind the 07-05 reset -- must not be included.
        assert db.bed_water_balance_cumulative_since_reset("bed1", "2026-07-07") == pytest.approx(-0.1)


class TestDayStats:
    def test_no_readings_returns_none(self, db):
        assert db.day_stats("temp_f", "2026-07-01T00:00:00+00:00", "2026-07-02T00:00:00+00:00") is None

    def test_reading_in_window_included(self, db):
        db.write_snapshot("2026-07-01T12:00:00+00:00", {"temp_f": (70.0, "F")}, {"raw": True})
        db.write_snapshot("2026-07-01T18:00:00+00:00", {"temp_f": (85.0, "F")}, {"raw": True})
        s = db.day_stats("temp_f", "2026-07-01T00:00:00+00:00", "2026-07-02T00:00:00+00:00")
        assert s["min"] == 70.0
        assert s["max"] == 85.0
        assert s["n"] == 2

    def test_reading_outside_window_excluded(self, db):
        db.write_snapshot("2026-06-30T23:00:00+00:00", {"temp_f": (40.0, "F")}, {"raw": True})  # before window
        db.write_snapshot("2026-07-01T12:00:00+00:00", {"temp_f": (70.0, "F")}, {"raw": True})  # in window
        db.write_snapshot("2026-07-02T01:00:00+00:00", {"temp_f": (90.0, "F")}, {"raw": True})  # after window
        s = db.day_stats("temp_f", "2026-07-01T00:00:00+00:00", "2026-07-02T00:00:00+00:00")
        assert s["min"] == 70.0
        assert s["max"] == 70.0
        assert s["n"] == 1
