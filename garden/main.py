"""
main.py — FastAPI application entry point.

Routes:
  GET  /              — dashboard HTML
  GET  /health        — liveness check
  POST /api/ecowitt   — Ecowitt-protocol ingest from GW1200
  GET  /api/latest    — latest reading per sensor (JSON)
  GET  /api/series    — time-series for one sensor (JSON)
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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


# ── GET /api/insights ────────────────────────────────────────────────────────

@app.get("/api/insights")
async def api_insights() -> JSONResponse:
    """
    Derived agronomic insights: VPD status, dew point / frost risk, heat index,
    ET₀ water balance, and per-bed crop stress.  Polled by the dashboard insight
    panel on the same 60 s refresh tick as /api/latest.
    """
    from garden.agent.weather import get_forecast

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
            # Sun times for sky animation — UTC epoch seconds; absent when location unavailable
            "sunrise_ts":          fc.get("sunrise_ts"),
            "sunset_ts":           fc.get("sunset_ts"),
            "sunrise_ts_tomorrow": fc.get("sunrise_ts_tomorrow"),
        }

    # ── Per-bed stress ────────────────────────────────────────────────────────
    src_temp_key = derived_cfg.get("source", {}).get("temp", "temp1_f")
    air_temp_row = latest_map.get(src_temp_key)
    air_temp_f   = air_temp_row["value"] if air_temp_row else None

    bed_results: list[dict[str, Any]] = []
    for bed in cfg.dashboard.get("beds", []):
        moist_key = bed.get("sensors", {}).get("soil_moisture")
        moist_row = latest_map.get(moist_key) if moist_key else None
        soil_moist = moist_row["value"] if moist_row else None

        if soil_moist is not None and air_temp_f is not None:
            stress = drv.bed_stress(
                bed.get("plants", []),
                soil_moist,
                air_temp_f,
                cfg.crops,
            )
        else:
            stress = {"status": "unknown", "reason": "Sensor data unavailable", "crops": []}

        bed_results.append({
            "id":   bed.get("id"),
            "name": bed.get("name"),
            **stress,
        })

    insights["beds"] = bed_results
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
        "dry":      cfg.thresholds.get("soil_moisture_low", {}).get("below", 30),
        "battLow":  cfg.thresholds.get("battery_low", {}).get("below", 1.1),
        "staleMin": cfg.watchdog.get("sensor_timeout_minutes", 30),
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
        _moist_min = max(_ranges[p]["moist"][0] for p in _unique)
        _moist_max = min(_ranges[p]["moist"][1] for p in _unique)
        _crop_labels = [_ranges[p]["label"] for p in _unique]
        _moisture_bands[_sensor_key] = {
            "min":   _moist_min,
            "max":   _moist_max,
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

    # Consolidated moisture/battery chart groups: one entry per bed, keyed to the
    # bed's sensor and carrying its plant list so the client can pick a dominant
    # emoji. Colors/emoji are assigned client-side for a stable per-bed palette.
    _moisture_group = []
    _batt_group = []
    for _bed in cfg.dashboard.get("beds", []):
        _sensors = _bed.get("sensors", {})
        _plants = _bed.get("plants", [])
        _name = _bed.get("name", _bed.get("id", ""))
        if _sensors.get("soil_moisture"):
            _moisture_group.append({"key": _sensors["soil_moisture"], "bed": _name, "plants": _plants})
        if _sensors.get("soil_battery"):
            _batt_group.append({"key": _sensors["soil_battery"], "bed": _name, "plants": _plants})

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
            "batt_group_json": json.dumps(_batt_group),
        },
    )
