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


class TestRawSeries:
    def test_returns_oldest_first_epoch_value_pairs(self, db):
        _write(db, "soilmoisture1", 40.0, 20)
        _write(db, "soilmoisture1", 55.0, 10)
        _write(db, "soilmoisture1", 50.0, 1)
        samples = db.raw_series("soilmoisture1", hours=1)
        assert [v for _, v in samples] == [40.0, 55.0, 50.0]
        # Timestamps strictly increase (oldest -> newest), unlike series()'s
        # ISO ts strings this returns raw epoch floats.
        epochs = [e for e, _ in samples]
        assert epochs == sorted(epochs)
        assert all(isinstance(e, float) for e in epochs)

    def test_not_bucket_averaged(self, db):
        # Two readings within the same 60s-bucket that series() would collapse
        # into one averaged point must both survive raw_series() distinctly.
        _write(db, "soilmoisture1", 30.0, 10.05)
        _write(db, "soilmoisture1", 60.0, 10.0)
        samples = db.raw_series("soilmoisture1", hours=1)
        assert len(samples) == 2

    def test_respects_hours_cutoff(self, db):
        _write(db, "soilmoisture1", 40.0, 5 * 60)  # 5h ago -- outside a 3h window
        _write(db, "soilmoisture1", 55.0, 60)      # 1h ago -- inside a 3h window
        samples = db.raw_series("soilmoisture1", hours=3)
        assert len(samples) == 1
        assert samples[0][1] == 55.0

    def test_thins_to_max_points_keeping_newest(self, db):
        for minutes_ago in range(100, 0, -1):
            _write(db, "soilmoisture1", float(100 - minutes_ago), minutes_ago)
        samples = db.raw_series("soilmoisture1", hours=2, max_points=20)
        assert 0 < len(samples) <= 20
        # The most recent reading (minutes_ago=1, value=99.0) must survive thinning.
        assert samples[-1][1] == 99.0

    def test_unknown_sensor_returns_empty(self, db):
        assert db.raw_series("nope") == []


class TestPruneOldData:
    def test_deletes_readings_and_snapshots_before_cutoff(self, db):
        _write(db, "temp_f", 60.0, 60 * 24 * 40)  # 40 days ago -- should be pruned
        _write(db, "temp_f", 75.0, 60)            # 1h ago -- should survive

        cutoff = _iso(60 * 24 * 30)  # 30 days ago
        readings_deleted, snapshots_deleted = db.prune_old_data(cutoff)

        assert readings_deleted == 1
        assert snapshots_deleted == 1
        rows = db.series("temp_f", hours=24 * 365)
        assert len(rows) == 1
        assert rows[0]["value"] == 75.0

    def test_nothing_to_prune_returns_zero(self, db):
        _write(db, "temp_f", 75.0, 60)
        cutoff = _iso(60 * 24 * 30)
        assert db.prune_old_data(cutoff) == (0, 0)

    def test_vacuum_does_not_raise(self, db):
        _write(db, "temp_f", 75.0, 60)
        db.vacuum()  # should not raise even with no prior DELETE in this connection
