"""
test_weather_forecast.py — verify the 7-day daily outlook that get_forecast()
builds from Open-Meteo's `daily` block (garden.agent.weather._build_daily).

Mocks httpx.get directly (same pattern as test_telegram_envelope.py) and
_get_latlon (to skip the zipcode geocode) rather than hitting the network.
Resets the module-level forecast cache before/after each test so one test's
fake response can't leak into the next via _cache/_cache_ts.
"""

import httpx
import pytest

from garden.agent import weather


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(weather, "_cache", {})
    monkeypatch.setattr(weather, "_cache_ts", 0.0)
    monkeypatch.setattr(weather, "_get_latlon", lambda: (42.0, -87.9))


def _fake_openmeteo_response(n_days: int = 7) -> httpx.Response:
    """A minimal Open-Meteo /v1/forecast response shaped like the real one,
    with `n_days` of daily data. et0_fao_evapotranspiration is in mm, same
    as the live API returns regardless of precipitation_unit=inch."""
    dates = [f"2026-07-{20 + i:02d}" for i in range(n_days)]
    return httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.open-meteo.com/v1/forecast"),
        json={
            "utc_offset_seconds": 0,
            "daily": {
                "time": dates,
                "temperature_2m_max": [88.0 + i for i in range(n_days)],
                "temperature_2m_min": [64.0 + i for i in range(n_days)],
                "precipitation_sum": [0.0] * n_days,
                "precipitation_probability_max": [10] * n_days,
                "wind_speed_10m_max": [8.0] * n_days,
                "weather_code": [1] * n_days,
                "et0_fao_evapotranspiration": [5.08] * n_days,  # 5.08mm == 0.2in exactly
                "sunrise": [f"{d}T06:00" for d in dates],
                "sunset":  [f"{d}T20:00" for d in dates],
            },
            "hourly": {
                "precipitation_probability": [0] * (24 * n_days),
                "temperature_2m": [70.0] * (24 * n_days),
            },
        },
    )


class TestDailyOutlook:
    def test_seven_days_returned(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_openmeteo_response(7))
        fc = weather.get_forecast()
        assert fc is not None
        assert len(fc["daily"]) == 7

    def test_day_zero_is_today(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_openmeteo_response(7))
        fc = weather.get_forecast()
        assert fc["daily"][0]["date"] == "2026-07-20"
        assert fc["daily"][0]["hi_f"] == 88.0
        assert fc["daily"][0]["lo_f"] == 64.0
        assert fc["daily"][0]["conditions"] == "mainly clear"

    def test_et0_converted_mm_to_inches_for_every_day(self, monkeypatch):
        """Regression guard: Open-Meteo always returns ET0 in mm regardless of
        precipitation_unit=inch (see the mm/25.4 conversion at index 0 in
        get_forecast() itself) -- _build_daily must apply the same conversion
        to every day in the array, not just the first."""
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_openmeteo_response(7))
        fc = weather.get_forecast()
        for day in fc["daily"]:
            assert day["et0_in"] == pytest.approx(0.2, abs=0.001)

    def test_missing_forecast_days_beyond_available_data(self, monkeypatch):
        # Open-Meteo could in principle return fewer days than requested;
        # _build_daily should not crash, just return what's there.
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_openmeteo_response(3))
        fc = weather.get_forecast()
        assert len(fc["daily"]) == 3

    def test_daily_absent_on_fetch_failure(self, monkeypatch):
        def _raise(*a, **k):
            raise httpx.ConnectError("boom")
        monkeypatch.setattr(httpx, "get", _raise)
        assert weather.get_forecast() is None

    def test_cache_hit_returns_independent_daily_copy(self, monkeypatch):
        """The cache-hit path must not hand callers a reference into the
        shared cache -- two calls' `daily` lists must be equal but distinct
        objects, so mutating one can never corrupt the cached copy."""
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_openmeteo_response(7))
        first = weather.get_forecast()
        second = weather.get_forecast()  # served from cache, no second httpx.get
        assert first["daily"] == second["daily"]
        assert first["daily"] is not second["daily"]
