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


def raw_series(
    sensor_key: str, hours: int = 48, max_points: int | None = None
) -> list[tuple[float, float]]:
    """
    Raw (epoch_seconds, value) pairs for one sensor over the last `hours`,
    oldest-first, NOT bucket-averaged.

    Unlike series(), which bucket-averages and smears watering spikes, this
    is for math that needs an un-smeared shape — drydown_rate() and
    analyze_watering() in derived.py. When `max_points` is set and the raw
    row count exceeds it, thin with a stride (keeping the newest rows) since
    those functions need the trend's shape, not every single reading.
    """
    cutoff = _hours_ago_iso(hours)
    with _conn() as con:
        rows = con.execute(
            """
            SELECT CAST(strftime('%s', ts) AS INTEGER) AS epoch, value
            FROM readings
            WHERE sensor_key = ?
              AND ts >= ?
            ORDER BY ts ASC
            """,
            (sensor_key, cutoff),
        ).fetchall()
    samples = [(float(r["epoch"]), r["value"]) for r in rows]
    if max_points and len(samples) > max_points:
        step = -(-len(samples) // max_points)  # ceil div
        # Stride from the end so the newest sample is always kept.
        thinned = samples[::-step][::-1]
        samples = thinned
    return samples


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

def prune_old_data(cutoff_iso: str) -> tuple[int, int]:
    """
    Delete readings and snapshots older than cutoff_iso (ISO-8601 UTC).
    readings.ts and snapshots.ts are always written identically (see write_snapshot),
    so a plain ts cutoff on each table is safe -- no orphaned readings result.
    Returns (readings_deleted, snapshots_deleted).
    """
    with _conn() as con:
        cur = con.execute("DELETE FROM readings WHERE ts < ?", (cutoff_iso,))
        readings_deleted = cur.rowcount
        cur = con.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff_iso,))
        snapshots_deleted = cur.rowcount
    return readings_deleted, snapshots_deleted


def vacuum() -> None:
    """Reclaim disk space freed by prune_old_data(). Rebuilds the whole file -- call sparingly."""
    with _conn() as con:
        con.execute("VACUUM")


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
