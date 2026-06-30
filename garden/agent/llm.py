"""
llm.py — LLM prose generation for alert messages and the daily morning brief.

Uses claude-haiku-4-5-20251001. Two public functions:

  write_alert(rule_id, sensor_key, title, fallback_body)
      Called when a deterministic rule fires. Returns an actionable Telegram
      message body. Weather context is injected when available; for watering
      rules the LLM is explicitly asked to estimate hose minutes and factor rain.
      Falls back to fallback_body if the API call fails.

  write_daily_brief(forecast, sensor_summary)
      Called once per morning by the cron tick. Returns a 4-6 sentence brief
      covering weather, per-bed status, and a watering plan for the day.
      Falls back to a plain-text summary if the API call fails.

The LLM never decides whether to alert — that's always the deterministic rules.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from garden import storage
from garden.config import cfg

log = logging.getLogger("garden.llm")

_CLIENT: anthropic.Anthropic | None = None


def _client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    return _CLIENT


# ── Context builders ──────────────────────────────────────────────────────────

def _recent_context(sensor_key: str) -> str:
    """Last 6 readings (up to 3h) for a sensor, oldest→newest."""
    if not sensor_key:
        return ""
    rows = storage.series(sensor_key, hours=3)
    if not rows:
        return ""
    vals = [f"{r['value']:.1f}{r['unit']}" for r in rows[-6:]]
    return f"Recent readings (oldest→newest): {', '.join(vals)}"


def _outdoor_temp_context() -> str:
    recent = storage.recent_values("temp_f", 1)
    if not recent:
        return ""
    return f"Current outdoor temperature: {recent[0]:.1f}°F"


def _weather_context() -> str:
    """Compact weather line, or empty string if weather is unavailable."""
    try:
        from garden.agent.weather import forecast_summary, get_forecast
        return forecast_summary(get_forecast())
    except Exception:
        return ""


def _derived_context() -> str:
    """VPD, dew point, and heat index — plant-stress signals for the LLM."""
    parts: list[str] = []
    try:
        from garden.derived import vpd_status, frost_risk
        from garden.config import cfg as _cfg

        vpd_vals = storage.recent_values("vpd_kpa", 1)
        if vpd_vals:
            v = vpd_vals[0]
            _status, _label = vpd_status(v, _cfg.derived.get("thresholds", {}))
            parts.append(f"VPD {v:.2f} kPa ({_label})")

        dp_vals = storage.recent_values("dewpoint_f", 1)
        if dp_vals:
            dp = dp_vals[0]
            is_frost, frost_msg = frost_risk(dp, _cfg.derived.get("frost_dewpoint_f", 35.6))
            if is_frost:
                parts.append(frost_msg)
            else:
                parts.append(f"Dew point {dp:.1f}°F")

        hi_vals = storage.recent_values("heatindex_f", 1)
        if hi_vals:
            parts.append(f"Feels like {hi_vals[0]:.1f}°F")
    except Exception:
        log.debug("_derived_context failed", exc_info=True)
    return ", ".join(parts) if parts else ""


# ── Alert prose ───────────────────────────────────────────────────────────────

_ALERT_SYSTEM = """\
You are a concise garden monitoring assistant. A sensor threshold has been breached.
Write a short, plain-English Telegram message (2-4 sentences) telling the gardener
exactly what is happening and what action to take. Be specific — include the actual
sensor reading. Do not use markdown, bullet points, or headers. Do not start with
"Alert" or repeat the title. Be direct and calm.\
"""

_WATERING_SYSTEM = """\
You are a concise garden monitoring assistant. A soil-moisture sensor is below the
watering threshold. Write a short, plain-English Telegram message (2-4 sentences) that:
1. States which bed needs water and the current moisture reading.
2. Estimates how many minutes to run a standard garden hose (~12 L/min flow) to
   recover the bed — use the moisture deficit and bed size you can infer from context.
3. Adjusts advice based on weather: if meaningful rain is expected in the next few
   hours, suggest waiting; in a heatwave (>95°F), advise watering deeper/longer.
Do not use markdown, bullet points, or headers. Be specific and practical.\
"""

_WATERED_SYSTEM = """\
You are a concise garden monitoring assistant. A soil-moisture sensor just recorded a
significant upward spike, meaning the garden bed was watered. Write a short, plain-English
Telegram message (2-3 sentences) that:
1. Confirms the bed was watered and shows the before/after moisture reading from Facts.
2. Notes whether the current level looks well-watered for the plants.
3. If weather context suggests watering wasn't needed (rain expected, high water balance),
   mention that briefly without being scolding.
Do not use markdown, bullet points, or headers. Be friendly and specific.\
"""


def write_alert(
    rule_id: str,
    sensor_key: str,
    title: str,
    fallback_body: str,
) -> str:
    """
    Returns LLM-written prose for the alert body.
    Falls back to fallback_body if the API call fails or key is missing.
    """
    if not cfg.anthropic_api_key:
        return fallback_body

    is_needs_water  = rule_id.startswith("soil_moisture_low") or rule_id.startswith("soil_moisture_rapid_drop")
    is_just_watered = rule_id.startswith("soil_moisture_rapid_rise")
    if is_needs_water:
        system = _WATERING_SYSTEM
    elif is_just_watered:
        system = _WATERED_SYSTEM
    else:
        system = _ALERT_SYSTEM

    label = cfg.sensor_label(sensor_key) if sensor_key else ""

    # Weather + temp context and sensor history are only useful for watering decisions.
    # Battery and temperature alerts have everything they need in fallback_body already.
    context_parts: list[str] = []
    if is_needs_water or is_just_watered:
        context_parts = [c for c in [
            _recent_context(sensor_key),
            _outdoor_temp_context(),
            _derived_context(),
            _weather_context(),
        ] if c]

    context_block = "\n".join(context_parts) if context_parts else ""

    user_prompt = f"""\
Rule: {title}
Sensor: {label or "gateway"}

{(context_block + chr(10) + chr(10)) if context_block else ""}\
Facts: {fallback_body}
"""

    try:
        response = _client().messages.create(
            model=cfg.llm.get("model", "claude-sonnet-4-6"),
            max_tokens=cfg.llm.get("max_tokens_alert", 220),
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        log.info("LLM alert prose for %s (%d chars)", rule_id, len(text))
        return text
    except Exception as exc:
        log.warning("LLM alert call failed for %s, using fallback: %s", rule_id, exc)
        return fallback_body


# ── Daily morning brief ───────────────────────────────────────────────────────

_BRIEF_SYSTEM = """\
You are a garden monitoring assistant. Format the morning brief as a structured Telegram
message using Telegram HTML (only <b>, <i>, <code> tags — no markdown, no other HTML).

Use exactly this layout — keep it tight, scannable, action-focused:

<b>☀️ Weather</b>
[one line: high/low, conditions, rain %, wind]

<b>🌱 Beds</b>
[one line per bed: name, moisture %, one-word status (Good / Thirsty / Dry)]

<b>💧 Watering plan</b>
[one line per bed that needs water: bed name, action, estimated hose minutes (~12 L/min)]
[if no beds need water, say "All beds OK, skip today"]
[if rain expected, say "Rain expected, hold off"]

<b>⚠️ Watch</b>
[only if there is something to flag: battery low, heat risk, frost, sensor issue]
[omit this section entirely if nothing to flag]

No prose paragraphs. No sign-off. Numbers over words. Be direct.\
"""


def write_daily_brief(
    forecast: dict[str, Any] | None,
    sensor_summary: str,
) -> str:
    """
    Generate a morning garden briefing. Returns LLM prose or a plain fallback.
    """
    if not cfg.anthropic_api_key:
        return _brief_fallback(forecast, sensor_summary)

    from garden.agent.weather import forecast_summary
    weather_line = forecast_summary(forecast)

    user_prompt = f"""\
Weather today: {weather_line}

Current garden sensor readings:
{sensor_summary}

Write the morning briefing.
"""

    try:
        response = _client().messages.create(
            model=cfg.llm.get("model", "claude-sonnet-4-6"),
            max_tokens=cfg.llm.get("max_tokens_brief", 320),
            system=_BRIEF_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        log.info("LLM morning brief generated (%d chars)", len(text))
        return text
    except Exception as exc:
        log.warning("LLM brief call failed, using fallback: %s", exc)
        return _brief_fallback(forecast, sensor_summary)


def _brief_fallback(
    forecast: dict[str, Any] | None,
    sensor_summary: str,
) -> str:
    """HTML-formatted fallback when the LLM is unavailable."""
    from garden.agent.weather import forecast_summary
    weather_line = forecast_summary(forecast)
    lines = [f"<b>☀️ Weather</b>\n{weather_line}", f"<b>🌱 Beds</b>\n{sensor_summary}"]
    return "\n\n".join(lines)
