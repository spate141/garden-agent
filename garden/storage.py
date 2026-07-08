"""
storage.py — SQLite schema, write, and query helpers.

Schema (long format):
  snapshots(id, ts, source_ok, raw_json)
    one row per Ecowitt POST

  readings(snapshot_id, ts, sensor_key, value, unit)
    one row per metric per POST
    indexed on (sensor_key, ts) for fast per-channel queries

  alert_state(rule_id, sensor_key, active, last_fired_ts)
    cooldown + alert-once-until-cleared bookkeeping (Stage 7)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from pathlib import Path
from typing import Any, Generator

log = logging.getLogger("garden.storage")

# Resolved lazily so tests can override DB_PATH before import
_db_path: Path | None = None


def db_path() -> Path:
    global _db_path
    if _db_path is None:
        from garden.config import cfg
        _db_path = cfg.db_path
    return _db_path


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    con = sqlite3.connect(db_path(), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,    -- ISO-8601 UTC
    source_ok   INTEGER NOT NULL DEFAULT 1,
    raw_json    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS readings (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    ts          TEXT    NOT NULL,
    sensor_key  TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_readings_key_ts ON readings(sensor_key, ts);

CREATE TABLE IF NOT EXISTS alert_state (
    rule_id       TEXT PRIMARY KEY,
    sensor_key    TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 0,   -- 1 = condition currently tripped
    last_fired_ts TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS bed_daily_agronomy (
    bed_id                    TEXT NOT NULL,
    local_date                TEXT NOT NULL,   -- YYYY-MM-DD in the configured timezone
    tmax_f                    REAL,
    tmin_f                    REAL,
    gdd_daily                 REAL,
    gdd_cumulative            REAL,
    et0_in                    REAL,
    etc_in                    REAL,
    rain_in                   REAL,
    irrigation_est_in         REAL,
    water_balance_daily       REAL,
    water_balance_cumulative  REAL,
    reset_reason              TEXT NOT NULL DEFAULT '',  -- 'good_soak' when water_balance_cumulative reset that day
    PRIMARY KEY (bed_id, local_date)
);
"""


def init_db() -> None:
    """Create tables if they don't exist. Safe to call multiple times."""
    with _conn() as con:
        con.executescript(_DDL)
    log.info("DB initialised at %s", db_path())


# ── write ─────────────────────────────────────────────────────────────────────

def write_snapshot(
    ts: str,
    metrics: dict[str, tuple[float, str]],  # {sensor_key: (value, unit)}
    raw: dict[str, Any],
    source_ok: bool = True,
) -> int:
    """
    Persist one Ecowitt snapshot.

    Returns the snapshot row id.
    """
    raw_json = json.dumps(raw)
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO snapshots(ts, source_ok, raw_json) VALUES (?,?,?)",
            (ts, int(source_ok), raw_json),
        )
        snap_id = cur.lastrowid
        con.executemany(
            "INSERT INTO readings(snapshot_id, ts, sensor_key, value, unit) VALUES (?,?,?,?,?)",
            [(snap_id, ts, key, val, unit) for key, (val, unit) in metrics.items()],
        )
    log.debug("Wrote snapshot %d (%d metrics)", snap_id, len(metrics))
    return snap_id


# ── query helpers ─────────────────────────────────────────────────────────────

def _hours_ago_iso(hours: int) -> str:
    """
    ISO-8601 UTC cutoff, `hours` hours before now, matching the format
    ingest.py writes to `ts` (e.g. "2026-07-05T14:30:00+00:00").

    We bind this as a parameter rather than using SQLite's
    datetime('now', '-N hours') because that renders "YYYY-MM-DD HH:MM:SS"
    (space separator, no offset) — a *lexicographic* comparison against our
    "T"-separated, offset-suffixed strings then breaks: 'T' (0x54) always
    sorts after ' ' (0x20), so any same-day row would satisfy `ts >= cutoff`
    regardless of the actual time, silently ignoring sub-day windows.
    """
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()


def health_info() -> dict[str, Any]:
    """
    Returns a dict for the /health endpoint:
      sensors_seen: count of distinct sensor keys in the DB
      last_reading_ts: ISO timestamp of the most recent reading (or null)
    """
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(DISTINCT sensor_key) as n, MAX(ts) as latest FROM readings"
        ).fetchone()
    return {
        "sensors_seen": row["n"],
        "last_reading_ts": row["latest"],
    }


def latest() -> list[dict[str, Any]]:
    """Latest value per sensor key (for dashboard current-values panel)."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT sensor_key, value, unit, ts
            FROM readings
            WHERE ts = (SELECT MAX(ts) FROM readings r2 WHERE r2.sensor_key = readings.sensor_key)
            ORDER BY sensor_key
            """
        ).fetchall()
    return [dict(r) for r in rows]


def series(sensor_key: str, hours: int = 24) -> list[dict[str, Any]]:
    """
    Time-series for one sensor over the last `hours` hours.

    Readings are ~60s apart, so wide windows (e.g. 7d) return thousands of
    rows per sensor. Bucket-average down to ~350 points so payloads stay
    small and fast to transfer/parse/draw; narrow windows (<=~6h) end up
    with a bucket smaller than the sample interval, so each bucket holds at
    most one row and the result is effectively unchanged (still one point
    per reading, just via the same aggregation path).
    """
    cutoff = _hours_ago_iso(hours)
    bucket_seconds = max(60, (hours * 3600) // 350)
    with _conn() as con:
        rows = con.execute(
            """
            SELECT MIN(ts) as ts, AVG(value) as value, MAX(unit) as unit
            FROM readings
            WHERE sensor_key = ?
              AND ts >= ?
            GROUP BY CAST(strftime('%s', ts) AS INTEGER) / ?
            ORDER BY ts
            """,
            (sensor_key, cutoff, bucket_seconds),
        ).fetchall()
    return [dict(r) for r in rows]


def last_seen(sensor_key: str) -> str | None:
    """ISO timestamp of the most recent reading for this key, or None."""
    with _conn() as con:
        row = con.execute(
            "SELECT MAX(ts) as ts FROM readings WHERE sensor_key = ?",
            (sensor_key,),
        ).fetchone()
    return row["ts"] if row else None


def recent_values(sensor_key: str, n: int) -> list[float]:
    """The n most recent values for a sensor key, newest-first."""
    with _conn() as con:
        rows = con.execute(
            "SELECT value FROM readings WHERE sensor_key = ? ORDER BY ts DESC LIMIT ?",
            (sensor_key, n),
        ).fetchall()
    return [r["value"] for r in rows]


def stats(sensor_key: str, hours: int = 24) -> dict[str, Any] | None:
    """
    Min/max/avg over the trailing `hours` window for one sensor key, plus the
    oldest reading in that window (so callers can compute their own "change
    over the window" delta against whatever they treat as "current").

    Returns None when there are zero readings for this sensor_key in the
    window (new sensor, DB just initialised, etc.) — callers must handle that.
    """
    cutoff = _hours_ago_iso(hours)
    with _conn() as con:
        agg = con.execute(
            """
            SELECT MIN(value) as min, MAX(value) as max, AVG(value) as avg, COUNT(*) as n
            FROM readings
            WHERE sensor_key = ? AND ts >= ?
            """,
            (sensor_key, cutoff),
        ).fetchone()
        if agg is None or agg["n"] == 0:
            return None
        oldest = con.execute(
            """
            SELECT value, ts FROM readings
            WHERE sensor_key = ? AND ts >= ?
            ORDER BY ts ASC LIMIT 1
            """,
            (sensor_key, cutoff),
        ).fetchone()
    return {
        "min": agg["min"],
        "max": agg["max"],
        "avg": round(agg["avg"], 3) if agg["avg"] is not None else None,
        "n": agg["n"],
        "oldest_value": oldest["value"] if oldest else None,
        "oldest_ts": oldest["ts"] if oldest else None,
    }


# ── alert_state helpers ───────────────────────────────────────────────────────

def get_alert_state(rule_id: str) -> dict[str, Any]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM alert_state WHERE rule_id = ?", (rule_id,)
        ).fetchone()
    if row is None:
        return {"rule_id": rule_id, "sensor_key": "", "active": 0, "last_fired_ts": ""}
    return dict(row)


def set_alert_state(
    rule_id: str, sensor_key: str, active: bool, last_fired_ts: str = ""
) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT INTO alert_state(rule_id, sensor_key, active, last_fired_ts)
            VALUES (?,?,?,?)
            ON CONFLICT(rule_id) DO UPDATE SET
                sensor_key    = excluded.sensor_key,
                active        = excluded.active,
                last_fired_ts = excluded.last_fired_ts
            """,
            (rule_id, sensor_key, int(active), last_fired_ts),
        )


# ── bed_daily_agronomy helpers (GDD + per-bed ET/water-balance accumulation) ──

_AGRONOMY_COLUMNS = (
    "tmax_f", "tmin_f", "gdd_daily", "gdd_cumulative",
    "et0_in", "etc_in", "rain_in", "irrigation_est_in",
    "water_balance_daily", "water_balance_cumulative", "reset_reason",
)


def get_bed_agronomy_latest(bed_id: str) -> dict[str, Any] | None:
    """Most recent bed_daily_agronomy row for a bed, or None if it has none yet."""
    with _conn() as con:
        row = con.execute(
            """
            SELECT * FROM bed_daily_agronomy
            WHERE bed_id = ?
            ORDER BY local_date DESC LIMIT 1
            """,
            (bed_id,),
        ).fetchone()
    return dict(row) if row else None


def upsert_bed_agronomy(bed_id: str, local_date: str, **fields: Any) -> None:
    """
    Insert or overwrite the (bed_id, local_date) row. Only keys in
    _AGRONOMY_COLUMNS are accepted, so callers must pass exactly the
    expected fields (matches set_alert_state's ON CONFLICT upsert style).
    """
    unknown = set(fields) - set(_AGRONOMY_COLUMNS)
    if unknown:
        raise ValueError(f"Unknown bed_daily_agronomy column(s): {sorted(unknown)}")

    columns = list(fields.keys())
    values = [fields[c] for c in columns]
    placeholders = ", ".join("?" for _ in columns)
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in columns)

    with _conn() as con:
        con.execute(
            f"""
            INSERT INTO bed_daily_agronomy(bed_id, local_date, {", ".join(columns)})
            VALUES (?, ?, {placeholders})
            ON CONFLICT(bed_id, local_date) DO UPDATE SET {update_clause}
            """,
            (bed_id, local_date, *values),
        )


def bed_agronomy_series(bed_id: str, days: int = 30) -> list[dict[str, Any]]:
    """Trailing `days` calendar days of bed_daily_agronomy rows, oldest → newest."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with _conn() as con:
        rows = con.execute(
            """
            SELECT * FROM bed_daily_agronomy
            WHERE bed_id = ? AND local_date >= ?
            ORDER BY local_date ASC
            """,
            (bed_id, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]
