"""
test_main_insights.py — End-to-end smoke test for GET /api/insights.

Uses FastAPI's TestClient for in-process ASGI calls — no live server/browser
(project rule: never spin up a dev server to self-check).  storage._conn()
opens a fresh sqlite3 connection per call, so DB_PATH is pointed at a real
temp file (same pattern as test_storage.py) rather than the ":memory:"
default, since writes and reads here need to share one database.
"""

import pytest
from fastapi.testclient import TestClient

from garden import storage


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_db_path", tmp_path / "test.sqlite3")
    storage.init_db()

    from garden.main import app
    return TestClient(app)


class TestApiInsightsForecast:
    def test_wind_max_mph_present_in_forecast_block(self, client, monkeypatch):
        """wind_max_mph is fetched by get_forecast() but was previously dropped
        before reaching the dashboard (see garden.dashboard.static.js sky
        animation, which needs it to drive cloud/rain drift speed)."""
        import garden.agent.weather as weather

        fake_forecast = {
            "et0_in": 0.12,
            "water_balance_in": 0.05,
            "frost_risk": False,
            "tomorrow_low_f": 55.0,
            "next_12h_peak_rain_pct": 10,
            "next_12h_peak_hour_offset": 3,
            "sunrise_ts": 1720000000,
            "sunset_ts": 1720040000,
            "sunrise_ts_tomorrow": 1720086400,
            "wind_max_mph": 17.5,
        }
        monkeypatch.setattr(weather, "get_forecast", lambda: fake_forecast)

        r = client.get("/api/insights")
        assert r.status_code == 200
        data = r.json()
        assert data["forecast"]["wind_max_mph"] == 17.5
