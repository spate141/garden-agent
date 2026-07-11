"""
test_main_insights.py — End-to-end smoke test for GET /api/insights, focused
on the per-bed "watering forecast" (insights["watering"]) added alongside the
"When to water next" dashboard card that replaced the Pressure & dew point
chart (see garden.main._bed_watering_forecast).

Uses FastAPI's TestClient for in-process ASGI calls — no live server/browser
(project rule: never spin up a dev server to self-check).  storage._conn()
opens a fresh sqlite3 connection per call, so DB_PATH is pointed at a real
temp file (same pattern as test_storage.py) rather than the ":memory:"
default, since writes and reads here need to share one database.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

from garden import storage


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_db_path", tmp_path / "test.sqlite3")
    storage.init_db()

    from garden.main import app
    return TestClient(app)


def _sawtooth(low: float, high: float, cycles: int = 12, points_per_cycle: int = 30) -> list[float]:
    vals: list[float] = []
    for _ in range(cycles):
        vals += [high - (high - low) * i / (points_per_cycle - 1) for i in range(points_per_cycle)]
    return vals


def _seed_drying_bed(sensor_key: str, low: float, high: float, hours_span: float = 48.0) -> None:
    """Write a monotonic drydown series (no rewatering) ending at `low`, so
    drydown_rate() has a clean downward trend to project from."""
    now = datetime.datetime.now(datetime.timezone.utc)
    n = 100
    step = hours_span * 3600 / n
    for i in range(n):
        value = high - (high - low) * i / (n - 1)
        ts = (now - datetime.timedelta(seconds=(n - i) * step)).isoformat()
        storage.write_snapshot(ts, {sensor_key: (value, "%")}, {"raw": True})


class TestApiInsightsWatering:
    def test_watering_field_present_with_one_entry_per_bed(self, client):
        r = client.get("/api/insights")
        assert r.status_code == 200
        data = r.json()
        assert "watering" in data
        # config.yaml ships 4 beds
        assert len(data["watering"]) == 4
        for entry in data["watering"]:
            assert set(entry) >= {"id", "name", "days", "label", "per_day", "remaining", "settling"}

    def test_drying_bed_yields_numeric_projection(self, client):
        _seed_drying_bed("soilmoisture1", low=38.0, high=55.0)
        storage.write_snapshot(
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            {"temp1_f": (75.0, "F")},
            {"raw": True},
        )
        r = client.get("/api/insights")
        watering = {w["id"]: w for w in r.json()["watering"]}
        bed1 = watering["bed1"]

        assert bed1["settling"] is False
        assert bed1["per_day"] > 0  # confirmed drying, not flat/rising
        assert bed1["remaining"] is not None
        assert 0.0 <= bed1["remaining"] <= 1.0

    def test_bed_without_history_reports_unknown_or_no_crash(self, client):
        # No readings at all for bed2/3/4 -- should degrade gracefully rather
        # than raising (soil_moist is None -> early-return branch).
        r = client.get("/api/insights")
        assert r.status_code == 200
        watering = {w["id"]: w for w in r.json()["watering"]}
        for bed_id in ("bed2", "bed3", "bed4"):
            assert watering[bed_id]["label"] in ("unknown", "2+ weeks", "not drying")
