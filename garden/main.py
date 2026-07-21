"""
main.py — FastAPI application entry point.

Routes:
  GET  /              — dashboard HTML
  GET  /health        — liveness check
  POST /api/ecowitt   — Ecowitt-protocol ingest from GW1200
  GET  /api/latest    — latest reading per sensor (JSON)
  GET  /api/series    — time-series for one sensor (JSON)
  POST /api/telegram  — inbound Telegram bot webhook (/bed1, /weather, ...)
"""

from __future__ import annotations

import json
import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from garden import bot
from garden import derived as drv
from garden import storage
from garden.agent import runner as agent_runner
from garden.config import cfg
from garden.ingest import IngestError, parse, validate_passkey

log = logging.getLogger("garden")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_STATIC = Path(__file__).parent / "dashboard" / "static"
_TEMPLATES = Jinja2Templates(
    directory=Path(__file__).parent / "dashboard" / "templates"
)

# ── lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    log.info("garden-agent started; DB at %s", storage.db_path())
    yield
    log.info("garden-agent shutting down")


app = FastAPI(title="garden-agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


# ── /health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    info: dict[str, Any] = storage.health_info()
    return JSONResponse({"status": "ok", **info})


# ── POST /api/ecowitt ─────────────────────────────────────────────────────────

@app.post("/api/ecowitt")
async def ecowitt_ingest(request: Request) -> JSONResponse:
    form = dict(await request.form())
    try:
        validate_passkey(form, cfg.ingest_passkey)
        ts, metrics, raw = parse(form)
    except IngestError as exc:
        return JSONResponse({"error": exc.detail}, status_code=exc.status)

    snap_id = storage.write_snapshot(ts, metrics, raw)
    agent_runner.evaluate_instant(snap_id, ts, metrics)
    return JSONResponse({"ok": True, "snapshot_id": snap_id, "metrics": len(metrics)})


# ── POST /api/telegram (inbound bot commands) ─────────────────────────────────

@app.post("/api/telegram")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """
    Telegram webhook target for inbound bot commands (/bed1, /weather, ...).

    Disabled (404) unless TELEGRAM_WEBHOOK_SECRET is configured. Requests must
    carry Telegram's secret-token header matching that value; handle_update()
    provides a second layer of defense by only acting on the owner's chat_id.
    Always returns 200 quickly — Telegram expects a fast ack — and processes
    the update in the background so an outbound reply never blocks this request.
    """
    if not cfg.telegram_webhook_secret:
        return JSONResponse({"error": "not found"}, status_code=404)

    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if header_secret != cfg.telegram_webhook_secret:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    update = await request.json()
    background_tasks.add_task(bot.handle_update, update)
    return JSONResponse({"ok": True})


# ── GET /api/latest ───────────────────────────────────────────────────────────

@app.get("/api/latest")
async def api_latest() -> JSONResponse:
    return JSONResponse(storage.latest())


# ── GET /api/series ───────────────────────────────────────────────────────────

@app.get("/api/series")
async def api_series(
    sensor: str = Query(..., description="sensor_key"),
    hours: int = Query(24, ge=1, le=168),
) -> JSONResponse:
    return JSONResponse(storage.series(sensor, hours))


# ── Per-bed moisture band (self-learned, crop-band fallback) ─────────────────

def _effective_bed_band(bed: dict[str, Any]) -> tuple[float, float] | None:
    """
    A bed's effective (min%, max%) moisture band: self-learned from its own
    recent history when there's enough of it, else the crop-derived band.

    Shared by /api/insights (per-bed stress) and the / dashboard handler
    (chart green band + "Too wet" line) so both agree on what "dry"/"wet"
    means for a given bed. See derived.effective_moisture_band for why a
    fixed crop range can permanently misjudge a bed with unusually
    loose/compacted soil.
    """
    moist_key = bed.get("sensors", {}).get("soil_moisture")
    if not moist_key:
        return None

    learn_cfg = cfg.thresholds.get("moisture_learning", {})
    if not learn_cfg.get("enabled", True):
        return drv.bed_moisture_band(bed.get("plants", []), cfg.crops)

    hours = int(learn_cfg.get("learn_days", 7)) * 24
    values = [r["value"] for r in storage.series(moist_key, hours)]

    return drv.effective_moisture_band(
        values,
        bed.get("plants", []),
        cfg.crops,
        learning_kwargs={
            "min_points":  learn_cfg.get("min_points", 200),
            "min_spread":  learn_cfg.get("min_spread_pct", 8),
            "dry_pctile":  learn_cfg.get("dry_pctile", 10),
            "wet_pctile":  learn_cfg.get("wet_pctile", 90),
            "dry_frac":    learn_cfg.get("dry_frac", 0.30),
            "wet_frac":    learn_cfg.get("wet_frac", 0.90),
        },
    )


def rain_covers(
    days: float | None, daily: list[dict[str, Any]] | None, rain_relief_in: float
) -> dict[str, Any]:
    """
    Does forecast rain arrive before a bed is projected to go dry?

    Scans forecast days from tomorrow (index 1, so today's already-observed
    rain isn't double counted) through ceil(days), looking for a single day
    with at least `rain_relief_in` of rain. A single-day threshold (not a
    cumulative sum) is deliberate: rain_relief_in worth of rain in one day
    reliably soaks the root zone, while the same total drizzled across
    several days often doesn't reach it.

    days=None/<=0 (no finite dry-date projection — "today"/"learning"/
    "not drying" already cover those cases) or an empty/missing forecast
    both mean nothing to offset against, so relief is always False then.
    """
    out = {"rain_relief": False, "rain_relief_day": None, "rain_relief_in": None}
    if not days or days <= 0 or not daily:
        return out

    horizon = min(7, math.ceil(days))
    for i in range(1, horizon + 1):
        if i >= len(daily):
            break
        day = daily[i]
        precip = day.get("precip_in") or 0.0
        if precip >= rain_relief_in:
            out["rain_relief"]     = True
            out["rain_relief_day"] = day.get("date")
            out["rain_relief_in"]  = precip
            break
    return out


def _bed_watering_forecast(
    moist_key: str,
    current_moist: float,
    band: tuple[float, float],
    fc: dict[str, Any] | None,
    drydown_hours: int,
    rain_relief_in: float,
) -> dict[str, Any]:
    """
    Rain-aware "when will this bed need water" projection for one bed.

    Reuses drydown_rate()/days_until_dry() from derived.py (previously
    unused server-side — a prior "when to water next" card was pulled for
    being noisy right after a watering event). The fix here is to surface
    the "learning"/"not drying"/"today" labels verbatim instead of forcing
    a number, and to let an upcoming-rain projection override the raw day
    count when relevant.
    """
    samples = storage.raw_series(moist_key, hours=drydown_hours, max_points=200)
    rate = drv.drydown_rate(samples)
    dud = drv.days_until_dry(current_moist, rate.get("per_day"), band[0])
    relief = rain_covers(dud["days"], fc.get("daily") if fc else None, rain_relief_in)
    return {
        "days_until_dry": dud["days"],
        "dry_label":      dud["label"],
        **relief,
    }


# ── GET /api/insights ────────────────────────────────────────────────────────

@app.get("/api/insights")
async def api_insights() -> JSONResponse:
    """
    Derived agronomic insights: VPD status, dew point / frost risk, heat index,
    ET₀ water balance, and per-bed crop stress.  Polled by the dashboard insight
    panel on the same 60 s refresh tick as /api/latest.
    """
    from garden.agent.weather import get_current, get_forecast

    latest_map: dict[str, Any] = {r["sensor_key"]: r for r in storage.latest()}
    derived_cfg = cfg.derived
    thresholds  = derived_cfg.get("thresholds", {})
    frost_thresh = derived_cfg.get("frost_dewpoint_f", 35.6)

    insights: dict[str, Any] = {}

    # ── VPD ──────────────────────────────────────────────────────────────────
    vpd_row = latest_map.get("vpd_kpa")
    if vpd_row:
        status, label = drv.vpd_status(vpd_row["value"], thresholds)
        insights["vpd"] = {
            "value": round(vpd_row["value"], 3),
            "status": status,
            "label": label,
        }

    # ── Dew point + frost risk ────────────────────────────────────────────────
    dp_row = latest_map.get("dewpoint_f")
    if dp_row:
        is_frost, frost_msg = drv.frost_risk(dp_row["value"], frost_thresh)
        insights["dewpoint"] = {
            "value": round(dp_row["value"], 1),
            "frost_risk": is_frost,
            "message": frost_msg,
        }

    # ── Heat index ───────────────────────────────────────────────────────────
    hi_row = latest_map.get("heatindex_f")
    if hi_row:
        insights["heat_index"] = {"value": round(hi_row["value"], 1)}

    # ── Weather / ET₀ water balance + frost lookahead ─────────────────────────
    fc = get_forecast()
    if fc:
        insights["forecast"] = {
            "et0_in":              fc.get("et0_in"),
            "water_balance_in":    fc.get("water_balance_in"),
            "frost_risk":          fc.get("frost_risk", False),
            "tomorrow_low_f":      fc.get("tomorrow_low_f"),
            # Rain lookahead — same fields forecast_summary() feeds to the LLM brief.
            "next_12h_peak_rain_pct":    fc.get("next_12h_peak_rain_pct"),
            "next_12h_peak_hour_offset": fc.get("next_12h_peak_hour_offset"),
            # Sun times for sky animation — UTC epoch seconds; absent when location unavailable
            "sunrise_ts":          fc.get("sunrise_ts"),
            "sunset_ts":           fc.get("sunset_ts"),
            "sunrise_ts_tomorrow": fc.get("sunrise_ts_tomorrow"),
            # Wind for sky animation — drives cloud/rain drift speed on the dashboard
            "wind_max_mph":        fc.get("wind_max_mph"),
            # 7-day outlook (day 0 = today) — dashboard water-balance chart + strip.
            "daily":               fc.get("daily", []),
        }

    # ── Live "right now" conditions (separate short-TTL cache from the daily forecast) ──
    cur = get_current()
    if cur:
        insights["current"] = {
            "conditions":      cur.get("conditions"),
            "weather_code":    cur.get("weather_code"),
            "rain_in":         cur.get("rain_in"),
            "cloud_cover_pct": cur.get("cloud_cover_pct"),
            "is_raining":      cur.get("is_raining", False),
            "intensity":       cur.get("intensity"),
        }

    # ── Per-bed stress ────────────────────────────────────────────────────────
    src_temp_key = derived_cfg.get("source", {}).get("temp", "temp1_f")
    air_temp_row = latest_map.get(src_temp_key)
    air_temp_f   = air_temp_row["value"] if air_temp_row else None

    wf_cfg          = cfg.dashboard.get("watering_forecast", {})
    wf_enabled      = wf_cfg.get("enabled", True)
    wf_drydown_hrs  = int(wf_cfg.get("drydown_hours", 48))
    wf_rain_relief  = float(wf_cfg.get("rain_relief_in", 0.25))

    bed_results: list[dict[str, Any]] = []
    for bed in cfg.dashboard.get("beds", []):
        moist_key = bed.get("sensors", {}).get("soil_moisture")
        moist_row = latest_map.get(moist_key) if moist_key else None
        soil_moist = moist_row["value"] if moist_row else None
        band = _effective_bed_band(bed)

        if soil_moist is not None and air_temp_f is not None:
            stress = drv.bed_stress(
                bed.get("plants", []),
                soil_moist,
                air_temp_f,
                cfg.crops,
                band_override=band,
            )
        else:
            stress = {
                "status": "unknown",
                "reason": "Sensor data unavailable",
                "detail": "Sensor data unavailable",
                "crops": [],
            }

        forecast_fields: dict[str, Any] = {}
        if wf_enabled and soil_moist is not None and band is not None and moist_key:
            forecast_fields = _bed_watering_forecast(
                moist_key, soil_moist, band, fc, wf_drydown_hrs, wf_rain_relief,
            )

        bed_results.append({
            "id":   bed.get("id"),
            "name": bed.get("name"),
            **stress,
            **forecast_fields,
        })

    insights["beds"] = bed_results

    # ── 24h min/max stats — scoped to only the sensors the UI actually renders ──
    stat_keys: set[str] = {"vpd_kpa"}
    weather_temp_key = cfg.dashboard.get("weather_keys", {}).get("temp")
    if weather_temp_key:
        stat_keys.add(weather_temp_key)
    for bed in cfg.dashboard.get("beds", []):
        moist_key = bed.get("sensors", {}).get("soil_moisture")
        if moist_key:
            stat_keys.add(moist_key)

    insights["stats"] = {
        k: s for k in stat_keys if (s := storage.stats(k, hours=24)) is not None
    }

    return JSONResponse(insights)


# ── GET / (dashboard) ─────────────────────────────────────────────────────────

@app.get("/")
async def dashboard(request: Request):
    latest_rows = storage.latest()
    latest_map = {r["sensor_key"]: r for r in latest_rows}

    # ordered keys: config.yaml order first, then any extra keys seen in the DB
    ordered = [k for k in cfg.sensors if k in latest_map]
    for r in latest_rows:
        if r["sensor_key"] not in ordered:
            ordered.append(r["sensor_key"])

    stats = [
        {
            "key": k,
            "label": cfg.sensor_label(k),
            "value": latest_map[k]["value"],
            "unit": latest_map[k]["unit"],
            "color": cfg.sensor_color(k),
        }
        for k in ordered
    ]

    _stat_groups_raw: list[dict] = cfg.dashboard.get("stat_groups", [])
    stat_groups: list[dict] = []
    for grp in _stat_groups_raw:
        members = [
            {
                "key": k,
                "label": cfg.sensor_label(k),
                "value": latest_map[k]["value"],
                "unit": latest_map[k]["unit"],
                "color": cfg.sensor_color(k),
            }
            for k in grp.get("sensors", [])
            if k in latest_map
        ]
        if members:
            stat_groups.append({"name": grp["name"], "sensors": members})

    charts = [
        {"key": k, "label": cfg.sensor_label(k), "color": cfg.sensor_color(k)}
        for k in ordered
        if cfg.sensors.get(k, {}).get("chart", True)
    ]

    last_ts = max((r["ts"] for r in latest_rows), default=None) if latest_rows else None

    # Thresholds read from config.yaml so the dashboard art agrees with the agent rules.
    # The same values drive check_soil_moisture_low, check_battery_low, check_watchdog.
    garden_thresholds = json.dumps({
        "dry":              cfg.thresholds.get("soil_moisture_low", {}).get("below", 30),
        "battLow":          cfg.thresholds.get("battery_low", {}).get("below", 1.1),
        "staleMin":         cfg.watchdog.get("sensor_timeout_minutes", 30),
        "rainLookaheadPct": cfg.weather.get("rain_lookahead_pct", 40),
    })

    # Band data for the upgraded charts and UI components.
    # moistureBands: per-bed optimal moisture range, derived by intersecting the
    #   ideal ranges of each bed's crops (same logic as drv.bed_stress). The
    #   too-wet marker is the band max.
    _moisture_bands: dict[str, Any] = {}
    for _bed in cfg.dashboard.get("beds", []):
        _sensor_key = _bed.get("sensors", {}).get("soil_moisture")
        if not _sensor_key:
            continue
        _plants = _bed.get("plants", [])
        _ranges: dict[str, Any] = {k: dict(v) for k, v in drv.CROP_RANGES.items()}
        for _crop, _overrides in cfg.crops.items():
            if _crop in _ranges and isinstance(_overrides, dict):
                _ranges[_crop] = {**_ranges[_crop], **_overrides}
        _unique = list(dict.fromkeys(p for p in _plants if p in _ranges))
        if not _unique:
            continue
        _crop_labels = [_ranges[p]["label"] for p in _unique]

        # Prefer the bed's self-learned band (compaction-adjusted) over the
        # flat crop range, so the green healthy band + "Too wet" line match
        # what /api/insights uses to label this bed dry/ok/wet.
        _band = _effective_bed_band(_bed)
        if _band is None:
            _band = (
                max(_ranges[p]["moist"][0] for p in _unique),
                min(_ranges[p]["moist"][1] for p in _unique),
            )

        _moisture_bands[_sensor_key] = {
            "min":   _band[0],
            "max":   _band[1],
            "label": _bed.get("name", _sensor_key),
            "crops": ", ".join(_crop_labels),
        }

    # vpdBands: four zones from derived.thresholds, keyed upTo breakpoints.
    _vpd_t = cfg.derived.get("thresholds", {})
    _vpd_bands = [
        {"upTo": _vpd_t.get("vpd_low",       0.4), "label": "Too low"},
        {"upTo": _vpd_t.get("vpd_high",       1.2), "label": "Healthy"},
        {"upTo": _vpd_t.get("vpd_very_high",  2.0), "label": "High stress"},
        {"upTo": 99,                                  "label": "Very high stress"},
    ]

    # battery: WH51 nominal ~1.5V; warn from alert threshold; critical is 85% of warn.
    _batt_warn = cfg.thresholds.get("battery_low", {}).get("below", 1.1)
    _batt_config: dict[str, Any] = {
        "nominal":  1.5,
        "warn":     _batt_warn,
        "critical": round(_batt_warn * 0.85, 2),
    }

    bands_json = json.dumps({
        "moistureBands": _moisture_bands,
        "vpdBands":      _vpd_bands,
        "battery":       _batt_config,
    })

    # Per-bed moisture chart metadata: one entry per bed, keyed to the bed's
    # sensor and carrying its plant list so the client can label the chart
    # with the bed's dominant-crop emoji.
    _moisture_group = []
    for _bed in cfg.dashboard.get("beds", []):
        _sensors = _bed.get("sensors", {})
        _plants = _bed.get("plants", [])
        _name = _bed.get("name", _bed.get("id", ""))
        _moisture_key = _sensors.get("soil_moisture")
        if _moisture_key:
            _moisture_group.append({
                "key": _moisture_key,
                "bed": _name,
                "plants": _plants,
                "color": cfg.sensor_color(_moisture_key),
            })

    # Sky animation: pass sunrise/sunset UTC epochs so the sun arc is correct on first paint.
    # Reuses the cached Open-Meteo forecast — no extra network call.
    from garden.agent.weather import get_forecast as _get_fc
    _fc = _get_fc()
    _sky: dict | None = None
    if _fc and _fc.get("sunrise_ts") and _fc.get("sunset_ts"):
        _sky = {
            "sunrise_ts":          _fc["sunrise_ts"],
            "sunset_ts":           _fc["sunset_ts"],
            "sunrise_ts_tomorrow": _fc.get("sunrise_ts_tomorrow"),
        }

    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "stats": stats,
            "charts": charts,
            "charts_json": json.dumps(charts),
            "last_ts": last_ts,
            "has_data": bool(latest_rows),
            "garden_thresholds": garden_thresholds,
            "tz_json": json.dumps(cfg.location.get("timezone", "America/Chicago")),
            "stat_groups": stat_groups,
            "beds_json": json.dumps(cfg.dashboard.get("beds", [])),
            "weather_keys_json": json.dumps(cfg.dashboard.get("weather_keys", {})),
            "sky_json": json.dumps(_sky),
            "bands_json": bands_json,
            "moisture_group_json": json.dumps(_moisture_group),
        },
    )
