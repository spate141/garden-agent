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

# ── display config ────────────────────────────────────────────────────────────

_SENSOR_ORDER = [
    "soilmoisture1", "soilmoisture2",
    "temp_f", "humidity",
    "baromrel_inhg",
    "temp_in_f", "humidityin",
    "soilbatt1", "soilbatt2",
]

_SENSOR_COLORS: dict[str, str] = {
    "soilmoisture1": "#4ade80",
    "soilmoisture2": "#22c55e",
    "temp_f":        "#fb923c",
    "humidity":      "#60a5fa",
    "temp_in_f":     "#fdba74",
    "humidityin":    "#93c5fd",
    "baromrel_inhg": "#a78bfa",
    "soilbatt1":     "#fbbf24",
    "soilbatt2":     "#f59e0b",
}

# sensor keys that appear as stats only — no time-series chart
_STATS_ONLY = {"soilbatt1", "soilbatt2", "humidityin", "temp_in_f"}


def _color(key: str) -> str:
    return _SENSOR_COLORS.get(key, "#888888")


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


# ── GET / (dashboard) ─────────────────────────────────────────────────────────

@app.get("/")
async def dashboard(request: Request):
    latest_rows = storage.latest()
    latest_map = {r["sensor_key"]: r for r in latest_rows}

    # ordered keys: priority list first, then anything else seen
    ordered = [k for k in _SENSOR_ORDER if k in latest_map]
    for r in latest_rows:
        if r["sensor_key"] not in ordered:
            ordered.append(r["sensor_key"])

    stats = [
        {
            "key": k,
            "label": cfg.sensor_label(k),
            "value": latest_map[k]["value"],
            "unit": latest_map[k]["unit"],
            "color": _color(k),
        }
        for k in ordered
    ]

    charts = [
        {"key": k, "label": cfg.sensor_label(k), "color": _color(k)}
        for k in ordered
        if k not in _STATS_ONLY
    ]

    last_ts = max((r["ts"] for r in latest_rows), default=None) if latest_rows else None

    # Thresholds read from config.yaml so the dashboard art agrees with the agent rules.
    # The same values drive check_soil_moisture_low, check_battery_low, check_watchdog.
    garden_thresholds = json.dumps({
        "dry":      cfg.thresholds.get("soil_moisture_low", {}).get("below", 30),
        "battLow":  cfg.thresholds.get("battery_low", {}).get("below", 1.1),
        "staleMin": cfg.watchdog.get("sensor_timeout_minutes", 30),
    })

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
        },
    )
