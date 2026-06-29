"""
ingest.py — Parse and normalise an Ecowitt-protocol form POST.

The GW1200 sends one POST per snapshot with all sensor channels as
form-encoded fields.  We:
  1. Validate PASSKEY against INGEST_PASSKEY (reject 401 otherwise).
  2. Parse the known numeric fields.
  3. Store values in US customary units (°F, inHg, mph, in) — no metric conversion.
  4. Return a snapshot dict ready for storage.write_snapshot().
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from garden.derived import dew_point_f, heat_index_f, vpd_kpa

log = logging.getLogger("garden.ingest")

# ── Ecowitt field → (storage key, unit, conversion) ──────────────────────────
# conversion: a callable float→float, or None for pass-through.
# All values stored in US customary units; no metric conversion needed.

# (storage_key, unit, convert_fn | None)
_FIELD_MAP: dict[str, tuple[str, str, Any]] = {
    # outdoor air — station sends °F; store as-is
    "tempf":        ("temp_f",          "°F",   None),
    "humidity":     ("humidity",        "%",    None),
    # indoor
    "tempinf":      ("temp_in_f",       "°F",   None),
    "humidityin":   ("humidityin",      "%",    None),
    # pressure — station sends inHg; store as-is
    "baromrelin":   ("baromrel_inhg",   "inHg", None),
    "baromabsin":   ("baromabs_inhg",   "inHg", None),
    # soil — channels 1-8 (only present channels will appear in the POST)
    **{f"soilmoisture{i}": (f"soilmoisture{i}", "%", None) for i in range(1, 9)},
    **{f"soilbatt{i}":     (f"soilbatt{i}",     "V", None) for i in range(1, 9)},
    # extra temp/humidity sensors (WN31 channels 1-8) — station sends °F
    **{f"temp{i}f":    (f"temp{i}_f",   "°F", None) for i in range(1, 9)},
    **{f"humidity{i}": (f"humidity{i}", "%",   None) for i in range(1, 9)},
    # wind — station sends mph; store as-is
    "winddir":      ("winddir",         "°",   None),
    "windspeedmph": ("windspeed_mph",   "mph", None),
    "windgustmph":  ("windgust_mph",    "mph", None),
    # UV / solar
    "uv":           ("uv_index",        "",    None),
    "solarradiation": ("solar_wm2",   "W/m²", None),
    # rain — station sends inches; store as-is
    "rainratein":   ("rainrate_inh",    "in/h", None),
    "dailyrainin":  ("rain_daily_in",   "in",   None),
}


def _compute_derived(metrics: dict[str, tuple[float, str]], derived_cfg: dict) -> None:
    """
    Compute VPD, dew point, and heat index from the configured temp+humidity pair
    and add them to `metrics` in-place so they are persisted alongside raw readings.

    derived_cfg: the cfg.derived dict from config.yaml.
    """
    if not derived_cfg.get("enabled", True):
        return

    source   = derived_cfg.get("source", {})
    temp_key = source.get("temp", "temp1_f")
    hum_key  = source.get("humidity", "humidity1")

    temp_val = metrics.get(temp_key)
    hum_val  = metrics.get(hum_key)
    if temp_val is None or hum_val is None:
        log.debug("Derived metrics skipped — source keys %s/%s not in snapshot", temp_key, hum_key)
        return

    temp_f, _ = temp_val
    rh, _     = hum_val

    try:
        metrics["dewpoint_f"]  = (round(dew_point_f(temp_f, rh), 1), "°F")
        metrics["vpd_kpa"]     = (round(vpd_kpa(temp_f, rh), 3),     "kPa")
        metrics["heatindex_f"] = (round(heat_index_f(temp_f, rh), 1), "°F")
        log.debug(
            "Derived: dewpoint=%.1f°F vpd=%.3fkPa heatindex=%.1f°F",
            metrics["dewpoint_f"][0], metrics["vpd_kpa"][0], metrics["heatindex_f"][0],
        )
    except Exception:
        log.warning("Derived metric computation failed", exc_info=True)


class IngestError(Exception):
    """Raised for validation failures — caller turns these into HTTP 4xx."""
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def validate_passkey(form: dict[str, str], expected: str) -> None:
    """Raise IngestError(401) if PASSKEY is missing or wrong."""
    got = form.get("PASSKEY", "")
    if got != expected:
        log.warning("Rejected POST: bad PASSKEY (got %r, want %r)", got, expected)
        raise IngestError(401, "Invalid PASSKEY")


def parse(form: dict[str, str]) -> tuple[str, dict[str, tuple[float, str]], dict[str, str]]:
    """
    Parse a raw Ecowitt form dict into normalised metrics.

    Returns:
        ts       — ISO-8601 UTC timestamp string
        metrics  — {storage_key: (value, unit)}
        raw      — the original form dict (for audit storage)
    """
    # Prefer the gateway's dateutc; fall back to server time.
    raw_ts = form.get("dateutc", "")
    try:
        ts = datetime.strptime(raw_ts, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).isoformat()
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc).isoformat()
        log.debug("dateutc missing or unparseable (%r); using server time", raw_ts)

    metrics: dict[str, tuple[float, str]] = {}
    for field, (storage_key, unit, convert) in _FIELD_MAP.items():
        raw_val = form.get(field)
        if raw_val is None:
            continue
        try:
            value = float(raw_val)
        except (ValueError, TypeError):
            log.debug("Skipping non-numeric field %s=%r", field, raw_val)
            continue
        metrics[storage_key] = (convert(value) if convert else value, unit)

    if not metrics:
        raise IngestError(400, "POST contained no recognisable sensor fields")

    # Append derived metrics (VPD, dew point, heat index) computed from the
    # configured temp+humidity source pair.  Failures are logged and silently
    # skipped so a bad sensor reading never breaks the ingest path.
    from garden.config import cfg  # deferred to avoid circular import at module load
    _compute_derived(metrics, cfg.derived)

    log.info("Parsed snapshot ts=%s, %d metrics", ts, len(metrics))
    return ts, metrics, dict(form)
