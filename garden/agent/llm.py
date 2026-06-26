"""
llm.py — LLM prose generation for alert messages.

Calls claude-haiku-4-5 to write a short, actionable Telegram message when a
rule fires. The LLM only writes prose — it never decides whether to alert.
Falls back to the rule's templated body if the API call fails.
"""

from __future__ import annotations

import logging

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


def _recent_context(sensor_key: str) -> str:
    """Build a short context string from the last 3h of readings for a sensor."""
    if not sensor_key:
        return ""
    rows = storage.series(sensor_key, hours=3)
    if not rows:
        return ""
    vals = [f"{r['value']:.1f}{r['unit']}" for r in rows[-6:]]
    return f"Recent readings (oldest→newest): {', '.join(vals)}"


def _outdoor_temp_context() -> str:
    recent = storage.recent_values("tempc", 1)
    if not recent:
        return ""
    return f"Current outdoor temperature: {recent[0]:.1f}°C"


_SYSTEM = """\
You are a concise garden monitoring assistant. When a sensor threshold is breached,
write a short, plain-English Telegram message (2-4 sentences max) that tells the
gardener exactly what is happening and what action to take. Be specific — include
the actual sensor reading. Do not use markdown, bullet points, or headers.
Do not start with "Alert" or repeat the title. Be direct and calm.\
"""


def write_alert(
    rule_id: str,
    sensor_key: str,
    title: str,
    fallback_body: str,
) -> str:
    """
    Returns LLM-written prose for the alert body.
    Falls back to fallback_body if the API call fails.
    """
    if not cfg.anthropic_api_key:
        return fallback_body

    label = cfg.sensor_label(sensor_key) if sensor_key else ""
    recent_ctx = _recent_context(sensor_key)
    temp_ctx = _outdoor_temp_context()

    context_lines = [c for c in [recent_ctx, temp_ctx] if c]
    context_block = "\n".join(context_lines) if context_lines else "No recent context available."

    user_prompt = f"""\
Rule triggered: {title}
Sensor: {label or "gateway"}
Rule ID: {rule_id}

{context_block}

Fallback message (use as a reference for facts, but rewrite naturally):
{fallback_body}
"""

    try:
        response = _client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        log.info("LLM prose generated for %s (%d chars)", rule_id, len(text))
        return text
    except Exception as exc:
        log.warning("LLM call failed for %s, using fallback: %s", rule_id, exc)
        return fallback_body
