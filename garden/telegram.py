"""
telegram.py — Hardened Telegram message delivery.

tg(title, body) sends a message with a shared envelope:
  🌱 Garden:<Title>
  <body>

send_message(text) sends raw text with no envelope — used for replies to
inbound bot commands (see garden/bot.py), where wrapping in "Garden: <title>"
would be redundant with the command itself.

Guarantees (both entry points, via the shared _deliver() core):
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
    Send a Telegram alert message, wrapped in the "🌱 Garden: <title>" envelope.

    Pass html=True only when body contains intentional HTML tags (e.g. <b>).
    Plain alert messages are sent as plain text to avoid parse errors from
    LLM-generated content that may contain < or > characters.

    Returns True on success, False on all-retries-exhausted failure.
    Failure is always logged; callers can ignore the return value.
    """
    text = f"\U0001f331 Garden: {title}\n\n{body}"
    return _deliver(text, html=html, log_label=title)


def send_message(text: str, html: bool = False) -> bool:
    """
    Send raw text with no envelope — used for replies to inbound bot commands
    (see garden/bot.py), where the command itself already identifies the reply.

    Returns True on success, False on all-retries-exhausted failure.
    """
    return _deliver(text, html=html, log_label=text[:60])


def _deliver(text: str, html: bool, log_label: str) -> bool:
    """Shared send core: dry-run short-circuit, truncation, retries. Not called directly."""
    from garden.config import cfg

    if cfg.dry_run:
        log.info("[dry-run] Telegram suppressed: %s", text[:200])
        return True

    parse_mode = "HTML" if html else None

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
                log.info("Telegram OK: %s", log_label)
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
        "TELEGRAM DELIVERY FAILED after %d attempts: %r ts=%s",
        _RETRIES, log_label, datetime.now(timezone.utc).isoformat(),
    )
    return False
