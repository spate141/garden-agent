"""
config.py — Load secrets.env and config.yaml into a single Config object.

Secrets come from secrets.env (gitignored). The YAML is versioned and
safe to commit. Both are loaded once at import time so every module can
do `from garden.config import cfg`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ── locate project root ───────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent  # garden-agent/

_secrets_path = _ROOT / "secrets.env"
_config_path = _ROOT / "config.yaml"


def _load() -> "_Config":
    # Load secrets.env if it exists; fall back gracefully so tests can mock env vars.
    if _secrets_path.exists():
        load_dotenv(_secrets_path)
    else:
        # In CI or tests, accept env vars already in the environment.
        load_dotenv()

    with _config_path.open() as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)

    return _Config(raw)


class _Config:
    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw

        # ── secrets ──────────────────────────────────────────────────────────
        self.ingest_passkey: str = self._require("INGEST_PASSKEY")
        self.telegram_token: str = self._require("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id: str = self._require("TELEGRAM_CHAT_ID")
        self.anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
        self.db_path: Path = Path(os.getenv("DB_PATH", "garden.sqlite3"))
        self.dry_run: bool = os.getenv("GARDEN_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

        # ── location (from secrets.env — gitignored, never in config.yaml) ──
        self.location: dict[str, str] = {
            "zipcode":  os.getenv("GARDEN_ZIPCODE", ""),
            "country":  os.getenv("GARDEN_COUNTRY", "us"),
            "timezone": os.getenv("GARDEN_TIMEZONE", "UTC"),
            "lat":      os.getenv("GARDEN_LAT", ""),
            "lon":      os.getenv("GARDEN_LON", ""),
        }

        # ── config.yaml subtrees (accessed as dicts by the rest of the code) ─
        self.sensors: dict[str, Any] = raw.get("sensors", {})
        self.dashboard: dict[str, Any] = raw.get("dashboard", {})
        self.thresholds: dict[str, Any] = raw.get("thresholds", {})
        self.watchdog: dict[str, Any] = raw.get("watchdog", {})
        self.cooldowns: dict[str, Any] = raw.get("cooldowns", {})
        self.llm: dict[str, Any] = raw.get("llm", {})
        self.weather: dict[str, Any] = raw.get("weather", {})
        self.daily_brief: dict[str, Any] = raw.get("daily_brief", {})
        self.derived: dict[str, Any] = raw.get("derived", {})
        self.crops: dict[str, Any] = raw.get("crops", {})

    # ── helpers ───────────────────────────────────────────────────────────────

    def _require(self, key: str) -> str:
        val = os.getenv(key, "")
        if not val:
            raise RuntimeError(
                f"Required env var {key!r} is missing. "
                f"Copy secrets.env.example → secrets.env and fill it in."
            )
        return val

    def sensor_label(self, key: str) -> str:
        """Return the human-readable label for a sensor key, or the key itself."""
        return self.sensors.get(key, {}).get("label", key)

    def sensor_color(self, key: str) -> str:
        """Return the hex color for a sensor key, or a neutral grey fallback."""
        return self.sensors.get(key, {}).get("color", "#888888")

    def bed_for_sensor(self, sensor_key: str) -> dict[str, Any] | None:
        """Bed dict whose soil-moisture or battery sensor is `sensor_key`, else None."""
        for bed in self.dashboard.get("beds", []):
            s = bed.get("sensors", {})
            if sensor_key and sensor_key in (s.get("soil_moisture"), s.get("soil_battery")):
                return bed
        return None

    def bed_crops_label(self, sensor_key: str) -> str:
        """Comma-joined crop families for the bed owning `sensor_key`, or '' if none (e.g. temp/gateway)."""
        bed = self.bed_for_sensor(sensor_key)
        if not bed:
            return ""
        from garden.derived import family_labels  # lazy import avoids any import cycle
        return ", ".join(family_labels(bed.get("plants", [])))


# Module-level singleton — import and use anywhere:
#   from garden.config import cfg
cfg = _load()
