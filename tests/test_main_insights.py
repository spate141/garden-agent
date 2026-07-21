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
from garden.main import rain_covers


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

    def test_daily_outlook_passed_through(self, client, monkeypatch):
        """forecast.daily (the 7-day outlook array from get_forecast()) must
        reach the dashboard for the water-outlook card to render."""
        import garden.agent.weather as weather

        fake_daily = [
            {"date": "2026-07-20", "weather_code": 1, "conditions": "mainly clear",
             "hi_f": 88.0, "lo_f": 64.0, "precip_in": 0.0, "precip_prob_pct": 5,
             "et0_in": 0.18, "wind_max_mph": 6.0},
        ]
        fake_forecast = {
            "et0_in": 0.18, "water_balance_in": -0.18, "frost_risk": False,
            "tomorrow_low_f": 62.0, "next_12h_peak_rain_pct": 5,
            "next_12h_peak_hour_offset": 0, "sunrise_ts": None, "sunset_ts": None,
            "sunrise_ts_tomorrow": None, "wind_max_mph": 6.0, "daily": fake_daily,
        }
        monkeypatch.setattr(weather, "get_forecast", lambda: fake_forecast)

        r = client.get("/api/insights")
        assert r.status_code == 200
        assert r.json()["forecast"]["daily"] == fake_daily

    def test_daily_defaults_to_empty_list_when_absent(self, client, monkeypatch):
        """Older/mocked forecast dicts without a "daily" key must not 500."""
        import garden.agent.weather as weather

        monkeypatch.setattr(weather, "get_forecast", lambda: {"et0_in": None, "water_balance_in": None})

        r = client.get("/api/insights")
        assert r.status_code == 200
        assert r.json()["forecast"]["daily"] == []


class TestRainCovers:
    """rain_covers() decides whether upcoming rain covers a bed's projected
    dry date -- pure logic, no config/storage, so it's unit-tested directly."""

    _daily = [
        {"date": "2026-07-20", "precip_in": 0.0},   # today (index 0, never scanned)
        {"date": "2026-07-21", "precip_in": 0.10},  # tomorrow -- below threshold
        {"date": "2026-07-22", "precip_in": 0.40},  # day after -- above threshold
        {"date": "2026-07-23", "precip_in": 0.0},
    ]

    def test_rain_within_horizon_covers(self):
        result = rain_covers(3.0, self._daily, rain_relief_in=0.25)
        assert result["rain_relief"] is True
        assert result["rain_relief_day"] == "2026-07-22"
        assert result["rain_relief_in"] == 0.40

    def test_rain_beyond_horizon_does_not_cover(self):
        # ceil(days)=1 -> only tomorrow (index 1, 0.10") is in scope; the
        # 0.40" day after (index 2) is beyond the horizon and must not count.
        result = rain_covers(1.0, self._daily, rain_relief_in=0.25)
        assert result["rain_relief"] is False

    def test_drizzle_below_threshold_does_not_cover(self):
        result = rain_covers(1.0, self._daily, rain_relief_in=0.25)
        assert result["rain_relief"] is False
        assert result["rain_relief_day"] is None

    def test_no_finite_dry_date_means_no_relief(self):
        # days=None ("today"/"learning") or days<=0 both mean nothing to
        # offset against -- there's no future date rain could beat.
        assert rain_covers(None, self._daily, rain_relief_in=0.25)["rain_relief"] is False
        assert rain_covers(0.0,  self._daily, rain_relief_in=0.25)["rain_relief"] is False

    def test_empty_or_missing_daily_means_no_relief(self):
        assert rain_covers(3.0, [], rain_relief_in=0.25)["rain_relief"] is False
        assert rain_covers(3.0, None, rain_relief_in=0.25)["rain_relief"] is False

    def test_today_index_never_scanned(self):
        # Even a huge today-only rain total must not count as "relief" --
        # today's rain is already reflected in the current sensor reading,
        # not a future event that "covers" the projected dry date.
        daily = [{"date": "2026-07-20", "precip_in": 5.0}]
        result = rain_covers(1.0, daily, rain_relief_in=0.25)
        assert result["rain_relief"] is False
