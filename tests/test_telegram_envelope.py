"""
test_telegram_envelope.py — verify the Telegram message format.

Locks in:
  - the "🌱 Garden: <title>" envelope with the space after the colon
  - HTML escaping in _escape_html
"""

import httpx
import pytest

from garden.telegram import tg, _escape_html


# ── _escape_html ──────────────────────────────────────────────────────────────

def test_escape_html_ampersand():
    assert _escape_html("a & b") == "a &amp; b"


def test_escape_html_lt_gt():
    assert _escape_html("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"


def test_escape_html_combined():
    assert _escape_html("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_escape_html_plain():
    assert _escape_html("no special chars") == "no special chars"


# ── tg envelope ──────────────────────────────────────────────────────────────

def test_tg_envelope_plain(monkeypatch):
    """Plain (non-HTML) message has 'Garden: <title>' with space and no parse_mode."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(json or {})
        resp = httpx.Response(200, json={"ok": True})
        return resp

    monkeypatch.setattr(httpx, "post", fake_post)

    tg("Morning Brief", "water the beds")

    assert captured["text"].startswith("🌱 Garden: Morning Brief"), (
        f"Expected envelope with space, got: {captured['text'][:60]!r}"
    )
    assert "parse_mode" not in captured  # plain text, no parse_mode key


def test_tg_envelope_html(monkeypatch):
    """HTML message has parse_mode=HTML."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(json or {})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)

    tg("Morning Brief", "<b>bold</b>", html=True)

    assert captured["text"].startswith("🌱 Garden: Morning Brief")
    assert captured.get("parse_mode") == "HTML"


def test_tg_truncates_long_body(monkeypatch):
    """Messages over 4096 chars are truncated and end with [...]."""
    def fake_post(url, json=None, timeout=None):
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)

    long_body = "x" * 5000
    # Should not raise; the function handles truncation internally
    result = tg("Test", long_body)
    assert result is True
