"""
test_telegram_webhook.py — unit tests for POST /api/telegram (garden/main.py).

Verifies the route is disabled (404) with no webhook secret configured, rejects
requests missing/mismatching Telegram's secret-token header (403), and otherwise
acks immediately while handing the update to garden.bot.handle_update().
"""

from fastapi.testclient import TestClient

from garden import bot, main
from garden.config import cfg

client = TestClient(main.app)

_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def test_webhook_disabled_when_no_secret_configured(monkeypatch):
    monkeypatch.setattr(cfg, "telegram_webhook_secret", "")
    resp = client.post("/api/telegram", json={"message": {}})
    assert resp.status_code == 404


def test_webhook_rejects_missing_secret_header(monkeypatch):
    monkeypatch.setattr(cfg, "telegram_webhook_secret", "s3cr3t")
    resp = client.post("/api/telegram", json={"message": {}})
    assert resp.status_code == 403


def test_webhook_rejects_wrong_secret_header(monkeypatch):
    monkeypatch.setattr(cfg, "telegram_webhook_secret", "s3cr3t")
    resp = client.post(
        "/api/telegram", json={"message": {}}, headers={_HEADER: "wrong"}
    )
    assert resp.status_code == 403


def test_webhook_accepts_correct_secret_and_dispatches(monkeypatch):
    monkeypatch.setattr(cfg, "telegram_webhook_secret", "s3cr3t")
    calls = []
    monkeypatch.setattr(bot, "handle_update", lambda update: calls.append(update))

    payload = {"message": {"chat": {"id": 1}, "text": "/help"}}
    resp = client.post(
        "/api/telegram", json=payload, headers={_HEADER: "s3cr3t"}
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert calls == [payload]
