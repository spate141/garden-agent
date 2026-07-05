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
