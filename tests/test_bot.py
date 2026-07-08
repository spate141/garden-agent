"""
test_bot.py — Unit tests for garden.bot (inbound Telegram commands).

garden.config.cfg loads the real config.yaml (beds bed1..bed4), so these tests
exercise the actual bed/crop configuration rather than a mock. storage.latest()
and the weather getters are monkeypatched to avoid DB/network dependencies.
"""

from datetime import datetime, timedelta, timezone

import pytest

from garden import bot
from garden.config import cfg


def _row(value: float, unit: str = "%", minutes_ago: float = 1.0) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {"value": value, "unit": unit, "ts": ts}


# ── dispatch: bed commands ────────────────────────────────────────────────────

def test_dispatch_bed4_names_its_crops(monkeypatch):
    monkeypatch.setattr(bot, "_latest_map", lambda: {
        "soilmoisture4": _row(53.0),
        "temp1_f": _row(80.0, unit="F"),
    })
    reply = bot.dispatch("bed4")
    assert "Bed 4" in reply
    assert "zucchini" in reply.lower()
    assert "eggplant" in reply.lower()
    assert "53" in reply


def test_dispatch_bed_with_no_data(monkeypatch):
    monkeypatch.setattr(bot, "_latest_map", lambda: {})
    reply = bot.dispatch("bed1")
    assert "Bed 1" in reply
    assert "No moisture data" in reply


def test_dispatch_unknown_bed_falls_through_to_help():
    reply = bot.dispatch("bed99")
    assert "Available commands" in reply


# ── dispatch: /beds ────────────────────────────────────────────────────────────

def test_dispatch_all_beds_lists_every_configured_bed(monkeypatch):
    monkeypatch.setattr(bot, "_latest_map", lambda: {
        "soilmoisture1": _row(60.0),
        "soilmoisture4": _row(53.0),
        "temp1_f": _row(80.0, unit="F"),
    })
    reply = bot.dispatch("beds")
    assert "Bed 1" in reply
    assert "Bed 4" in reply
    assert "no data" in reply.lower()  # beds 2/3 have no monkeypatched reading


# ── dispatch: /weather, /air ───────────────────────────────────────────────────

def test_dispatch_weather(monkeypatch):
    monkeypatch.setattr("garden.agent.weather.get_forecast", lambda: None)
    monkeypatch.setattr("garden.agent.weather.get_current", lambda: None)
    reply = bot.dispatch("weather")
    assert "Weather" in reply


def test_dispatch_air_no_data(monkeypatch):
    monkeypatch.setattr(bot, "_latest_map", lambda: {})
    reply = bot.dispatch("air")
    assert "No air data" in reply


def test_dispatch_air_with_readings(monkeypatch):
    monkeypatch.setattr(bot, "_latest_map", lambda: {
        "vpd_kpa": _row(2.38, unit="kPa"),
        "dewpoint_f": _row(60.0, unit="F"),
        "heatindex_f": _row(86.0, unit="F"),
    })
    reply = bot.dispatch("air")
    assert "VPD" in reply
    assert "2.38" in reply


# ── dispatch: /help fallback ───────────────────────────────────────────────────

def test_dispatch_help_lists_bed_and_static_commands():
    reply = bot.dispatch("help")
    assert "/bed1" in reply
    assert "/weather" in reply
    assert "/brief" in reply


def test_dispatch_unrecognized_command_is_help():
    assert bot.dispatch("nonsense") == bot._help()


# ── command_menu ───────────────────────────────────────────────────────────────

def test_command_menu_has_one_entry_per_bed_plus_static():
    menu = bot.command_menu()
    commands = {m["command"] for m in menu}
    n_beds = len(cfg.dashboard.get("beds", []))
    assert {"bed1", "bed2", "bed3", "bed4"} <= commands
    assert {"beds", "weather", "air", "brief", "help"} <= commands
    assert len(menu) == n_beds + 5


# ── handle_update: owner-only guard + parsing ─────────────────────────────────

def test_handle_update_ignores_non_owner_chat(monkeypatch):
    sent = []
    monkeypatch.setattr("garden.telegram.send_message", lambda text, html=False: sent.append(text))

    update = {"message": {"chat": {"id": 111111}, "text": "/beds"}}
    bot.handle_update(update)

    assert sent == []


def test_handle_update_replies_for_owner_chat(monkeypatch):
    sent = []
    monkeypatch.setattr("garden.telegram.send_message", lambda text, html=False: sent.append(text))
    monkeypatch.setattr(bot, "_latest_map", lambda: {})

    update = {"message": {"chat": {"id": int(cfg.telegram_chat_id)}, "text": "/help"}}
    bot.handle_update(update)

    assert len(sent) == 1
    assert "Available commands" in sent[0]


def test_handle_update_strips_bot_username_suffix(monkeypatch):
    sent = []
    monkeypatch.setattr("garden.telegram.send_message", lambda text, html=False: sent.append(text))
    monkeypatch.setattr(bot, "_latest_map", lambda: {})

    update = {"message": {"chat": {"id": int(cfg.telegram_chat_id)}, "text": "/help@my_garden_bot"}}
    bot.handle_update(update)

    assert len(sent) == 1
    assert "Available commands" in sent[0]


def test_handle_update_ignores_non_command_text(monkeypatch):
    sent = []
    monkeypatch.setattr("garden.telegram.send_message", lambda text, html=False: sent.append(text))

    update = {"message": {"chat": {"id": int(cfg.telegram_chat_id)}, "text": "hello there"}}
    bot.handle_update(update)

    assert sent == []


def test_handle_update_command_error_sends_fallback_message(monkeypatch):
    sent = []
    monkeypatch.setattr("garden.telegram.send_message", lambda text, html=False: sent.append(text))

    def _boom(_command):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot, "dispatch", _boom)

    update = {"message": {"chat": {"id": int(cfg.telegram_chat_id)}, "text": "/beds"}}
    bot.handle_update(update)

    assert len(sent) == 1
    assert "went wrong" in sent[0]
