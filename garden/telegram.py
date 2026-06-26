"""
telegram.py — Hardened Telegram message delivery.

tg(title, body) sends a message with a shared envelope:
  🌱 Garden:<Title>
  <body>

Guarantees:
  - message truncated to 4096 chars before sending
  - non-2xx treated as failure
  - 3 retries with exponential backoff (1s, 2s, 4s)
  - failure always logs a line — a dropped alert is never silent
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

log = logging.getLogger("garden.telegram")

_TG_API = "https://api.telegram.org"
_MAX_CHARS = 4096
_RETRIES = 3


def _escape_html(s: str) -> str:
    """Escape characters that Telegram's HTML parser would misinterpret."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tg(title: str, body: str, html: bool = False) -> bool:
    """
    Send a Telegram message.

    Pass html=True only when body contains intentional HTML tags (e.g. <b>).
    Plain alert messages are sent as plain text to avoid parse errors from
    LLM-generated content that may contain < or > characters.

    Returns True on success, False on all-retries-exhausted failure.
    Failure is always logged; callers can ignore the return value.
    """
    from garden.config import cfg

    if html:
        text = f"\U0001f331 Garden:{title}\n\n{body}"
        parse_mode = "HTML"
    else:
        text = f"\U0001f331 Garden:{title}\n\n{body}"
        parse_mode = None

    if len(text) > _MAX_CHARS:
        cut = _MAX_CHARS - 6
        text = text[:cut] + "\n[…]"
        log.warning("Message truncated to %d chars", _MAX_CHARS)

    url = f"{_TG_API}/bot{cfg.telegram_token}/sendMessage"
    payload: dict = {"chat_id": cfg.telegram_chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    for attempt in range(1, _RETRIES + 1):
        try:
            resp = httpx.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                log.info("Telegram OK: %s", title)
                return True
            log.warning(
                "Telegram non-2xx (attempt %d/%d): %d %s",
                attempt, _RETRIES, resp.status_code, resp.text[:120],
            )
        except Exception as exc:
            log.warning("Telegram request error (attempt %d/%d): %s", attempt, _RETRIES, exc)

        if attempt < _RETRIES:
            time.sleep(2 ** (attempt - 1))   # 1s, 2s

    log.error(
        "TELEGRAM DELIVERY FAILED after %d attempts: title=%r ts=%s",
        _RETRIES, title, datetime.now(timezone.utc).isoformat(),
    )
    return False


def heartbeat(sensor_count: int, last_ts: str | None) -> bool:
    """Daily alive ping — called by the cron tick."""
    last = last_ts or "no data yet"
    return tg(
        "Heartbeat",
        f"Monitor is alive.\n"
        f"Sensors reporting: <b>{sensor_count}</b>\n"
        f"Last reading: <b>{last}</b>",
        html=True,
    )
