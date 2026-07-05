"""
weather.py — Open-Meteo forecast client with lightweight in-process caching.

No API key required. Location comes from secrets.env:
  GARDEN_ZIPCODE / GARDEN_COUNTRY  → geocoded once via Zippopotam.us
  GARDEN_LAT / GARDEN_LON          → skip geocoding if set directly

All failures log a warning and return None — weather is always optional.
The brief and watering alerts degrade gracefully without it.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("garden.weather")


# ── WMO weather code → human description ─────────────────────────────────────

_WMO: dict[int, str] = {
    0:  "clear sky",
    1:  "mainly clear",
    2:  "partly cloudy",
    3:  "overcast",
    45: "foggy",
    48: "freezing fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    80: "rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}

# WMO codes that mean "precipitation is actively falling" and the subset of
# those that count as heavy enough to escalate the dashboard's rain visual.
_RAIN_CODES: set[int] = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}
_HEAVY_CODES: set[int] = {65, 82, 95, 96, 99}


# ── Geocoding cache (resolved once per process) ───────────────────────────────

_latlon: tuple[float, float] | None = None


def _get_latlon() -> tuple[float, float] | None:
    global _latlon
    if _latlon is not None:
        return _latlon

    from garden.config import cfg

    # Direct coordinate override — skip geocoding
    lat_s = cfg.location.get("lat", "")
    lon_s = cfg.location.get("lon", "")
    if lat_s and lon_s:
        try:
            _latlon = (float(lat_s), float(lon_s))
            log.info("Using direct coordinates: %.4f, %.4f", *_latlon)
            return _latlon
        except ValueError:
            log.warning("Invalid GARDEN_LAT/GARDEN_LON: %r, %r", lat_s, lon_s)

    # Geocode from zipcode
    zipcode = cfg.location.get("zipcode", "")
    country = cfg.location.get("country", "us")
    if not zipcode:
        log.warning("GARDEN_ZIPCODE not set — weather forecast unavailable")
        return None

    try:
        r = httpx.get(
            f"https://api.zippopotam.us/{country}/{zipcode}",
            timeout=8,
            follow_redirects=True,
        )
        if r.status_code != 200:
            log.warning("Geocoding failed: HTTP %d for zip %s/%s", r.status_code, country, zipcode)
            return None
        data = r.json()
        place = data["places"][0]
        lat = float(place["latitude"])
        lon = float(place["longitude"])
        _latlon = (lat, lon)
        log.info(
            "Geocoded %s/%s → %.4f, %.4f (%s, %s)",
            country, zipcode, lat, lon,
            place.get("place name", ""),
            place.get("state abbreviation", ""),
        )
        return _latlon
    except Exception as exc:
        log.warning("Geocoding error: %s", exc)
        return None


# ── Forecast cache ────────────────────────────────────────────────────────────

_cache: dict[str, Any] = {}
_cache_ts: float = 0.0


def get_forecast() -> dict[str, Any] | None:
    """
    Fetch today's Open-Meteo forecast. Returns a normalized dict or None.
    Cached in-process for cfg.weather['cache_minutes'] (default 120 min).

    Returned dict keys:
      today_high_f, today_low_f        — °F
      precip_in                        — total precip today (inches)
      precip_prob_pct                  — max precip probability today (%)
      wind_max_mph                     — max wind today (mph)
      weather_code, conditions         — WMO code + human text
      next_12h_peak_rain_pct           — highest rain probability in next 12h (%)
      next_12h_peak_hour_offset        — hours from now until that peak
    """
    global _cache, _cache_ts

    from garden.config import cfg

    if not cfg.weather.get("enabled", True):
        return None

    cache_minutes = cfg.weather.get("cache_minutes", 120)
    now_mono = time.monotonic()
    if _cache and (now_mono - _cache_ts) < cache_minutes * 60:
        return dict(_cache)

    coords = _get_latlon()
    if coords is None:
        return None

    lat, lon = coords
    tz = cfg.location.get("timezone", "UTC")

    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":         lat,
                "longitude":        lon,
                "temperature_unit":   "fahrenheit",
                "wind_speed_unit":    "mph",
                "precipitation_unit": "inch",
                "daily": (
                    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                    "precipitation_probability_max,wind_speed_10m_max,weather_code,"
                    "et0_fao_evapotranspiration,sunrise,sunset"
                ),
                "hourly": "precipitation_probability,temperature_2m",
                "forecast_days": 2,   # day 0 = today, day 1 = tomorrow (frost lookahead)
                "timezone": tz,
            },
            timeout=10,
            follow_redirects=True,
        )
        r.raise_for_status()
    except Exception as exc:
        log.warning("Open-Meteo fetch failed: %s", exc)
        return None

    try:
        raw   = r.json()
        daily  = raw["daily"]
        hourly = raw["hourly"]

        precip_today = daily["precipitation_sum"][0] or 0.0

        # ET₀ is always returned in mm (Open-Meteo ignores precipitation_unit for ET₀)
        et0_mm   = daily.get("et0_fao_evapotranspiration", [None])[0]
        et0_in   = round(et0_mm / 25.4, 3) if et0_mm is not None else None

        fc: dict[str, Any] = {
            "today_high_f":    daily["temperature_2m_max"][0],
            "today_low_f":     daily["temperature_2m_min"][0],
            "precip_in":       precip_today,
            "precip_prob_pct": daily["precipitation_probability_max"][0],
            "wind_max_mph":    daily["wind_speed_10m_max"][0],
            "weather_code":    daily["weather_code"][0],
            "conditions":      _WMO.get(int(daily["weather_code"][0]), "unknown"),
            # ET₀ / water balance
            "et0_in":          et0_in,
            "water_balance_in": (
                round(precip_today - et0_in, 3) if et0_in is not None else None
            ),
            # Tomorrow's forecast (frost lookahead — day index 1)
            "tomorrow_low_f":  (
                daily["temperature_2m_min"][1]
                if len(daily.get("temperature_2m_min", [])) > 1 else None
            ),
        }

        # Frost risk: tomorrow night's low ≤ configured threshold
        from garden.config import cfg as _cfg  # deferred — avoid circular at module level
        frost_thresh = _cfg.derived.get("frost_dewpoint_f", 35.6)
        tomorrow_low = fc["tomorrow_low_f"]
        fc["frost_risk"] = bool(tomorrow_low is not None and tomorrow_low <= frost_thresh)

        # Find the rainiest window in the next 12h
        current_hour = datetime.now(timezone.utc).astimezone().hour
        h_prob = hourly.get("precipitation_probability", [])
        next_12 = h_prob[current_hour: current_hour + 12]
        if next_12:
            peak = max(next_12)
            fc["next_12h_peak_rain_pct"]      = peak
            fc["next_12h_peak_hour_offset"]   = next_12.index(peak)
        else:
            fc["next_12h_peak_rain_pct"]      = 0
            fc["next_12h_peak_hour_offset"]   = 0

        # Sun rise/set as UTC epoch seconds (for dashboard sky animation).
        # Open-Meteo returns naive local ISO strings; utc_offset_seconds converts them to UTC.
        # Formula: treat string as UTC, then subtract the offset to arrive at true UTC.
        utc_offset = raw.get("utc_offset_seconds", 0)

        def _sun_epoch(s: str) -> int:
            return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()) - utc_offset

        try:
            sr = daily.get("sunrise", [])
            ss = daily.get("sunset",  [])
            if sr and ss:
                fc["sunrise_ts"]          = _sun_epoch(sr[0])
                fc["sunset_ts"]           = _sun_epoch(ss[0])
                fc["sunrise_ts_tomorrow"] = _sun_epoch(sr[1]) if len(sr) > 1 else None
        except Exception as _exc:
            log.debug("Sun time parse error: %s", _exc)

        _cache    = fc
        _cache_ts = now_mono

        log.info(
            "Forecast: %s, %.1f°F/%.1f°F, rain %.0f%% / %.2f in",
            fc["conditions"], fc["today_high_f"], fc["today_low_f"],
            fc["precip_prob_pct"], fc["precip_in"],
        )
        return dict(fc)

    except Exception as exc:
        log.warning("Open-Meteo parse error: %s", exc)
        return None


# ── Current-conditions cache (short TTL — this is the "right now" feed) ──────

_current_cache: dict[str, Any] = {}
_current_cache_ts: float = 0.0


def get_current() -> dict[str, Any] | None:
    """
    Fetch Open-Meteo's live current-conditions block — separate from the daily
    forecast because it needs a much shorter cache to answer "is it raining
    right now" instead of "what's expected today".

    Cached in-process for cfg.weather['current_cache_minutes'] (default 15 min).

    Returned dict keys:
      weather_code, conditions   — WMO code + human text
      rain_in, precip_in         — current instantaneous rain / total precip (inches)
      cloud_cover_pct            — 0-100
      is_raining                 — bool, precip actively falling right now
      intensity                 — 'heavy' | 'light' (meaningful only when is_raining)
    """
    global _current_cache, _current_cache_ts

    from garden.config import cfg

    if not cfg.weather.get("enabled", True):
        return None

    cache_minutes = cfg.weather.get("current_cache_minutes", 15)
    now_mono = time.monotonic()
    if _current_cache and (now_mono - _current_cache_ts) < cache_minutes * 60:
        return dict(_current_cache)

    coords = _get_latlon()
    if coords is None:
        return None

    lat, lon = coords
    tz = cfg.location.get("timezone", "UTC")

    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":         lat,
                "longitude":        lon,
                "temperature_unit":   "fahrenheit",
                "precipitation_unit": "inch",
                "current": "weather_code,precipitation,rain,cloud_cover",
                "timezone": tz,
            },
            timeout=10,
            follow_redirects=True,
        )
        r.raise_for_status()
    except Exception as exc:
        log.warning("Open-Meteo current-conditions fetch failed: %s", exc)
        return None

    try:
        raw     = r.json()
        current = raw["current"]

        weather_code = int(current["weather_code"])
        rain_in      = current.get("rain", 0.0) or 0.0
        precip_in    = current.get("precipitation", 0.0) or 0.0
        is_raining   = weather_code in _RAIN_CODES or rain_in > 0 or precip_in > 0

        cur: dict[str, Any] = {
            "weather_code":     weather_code,
            "conditions":       _WMO.get(weather_code, "unknown"),
            "rain_in":          rain_in,
            "precip_in":        precip_in,
            "cloud_cover_pct":  current.get("cloud_cover"),
            "is_raining":       is_raining,
            "intensity":        "heavy" if weather_code in _HEAVY_CODES else "light",
        }

        _current_cache    = cur
        _current_cache_ts = now_mono

        log.info(
            "Current conditions: %s, raining=%s (%s), cloud cover %s%%",
            cur["conditions"], cur["is_raining"], cur["intensity"], cur["cloud_cover_pct"],
        )
        return dict(cur)

    except Exception as exc:
        log.warning("Open-Meteo current-conditions parse error: %s", exc)
        return None


def forecast_summary(fc: dict[str, Any] | None) -> str:
    """Compact weather summary for LLM context blocks (watering alerts + daily brief)."""
    if not fc:
        return "Weather: unavailable."

    from garden.config import cfg
    rain_lookahead_pct = cfg.weather.get("rain_lookahead_pct", 40)

    rain = f"{fc['precip_prob_pct']:.0f}% chance, {fc['precip_in']:.2f} in"
    note = ""
    if fc.get("next_12h_peak_rain_pct", 0) >= rain_lookahead_pct:
        hrs = fc["next_12h_peak_hour_offset"]
        note = f" Rain likely within ~{hrs + 1}h."

    parts = [
        f"Today: {fc['today_high_f']:.1f}°F high / {fc['today_low_f']:.1f}°F low, "
        f"{fc['conditions']}, rain {rain}, "
        f"wind to {fc['wind_max_mph']:.0f} mph.{note}"
    ]

    # ET₀ water balance — irrigation guidance for the LLM
    wb = fc.get("water_balance_in")
    if wb is not None:
        if wb < -0.05:
            parts.append(
                f"Water balance: -{abs(wb):.2f}\" deficit (ET0 exceeds rain), beds likely need irrigation."
            )
        elif wb > 0.05:
            parts.append(
                f"Water balance: +{wb:.2f}\" surplus (rain exceeds ET0), skip irrigation today."
            )
        else:
            parts.append("Water balance: roughly even, monitor soil moisture.")

    # Frost lookahead
    if fc.get("frost_risk"):
        low = fc.get("tomorrow_low_f")
        low_str = f" (low {low:.0f}°F)" if low is not None else ""
        parts.append(f"Frost risk tonight{low_str}, protect tender plants.")

    return " ".join(parts)
