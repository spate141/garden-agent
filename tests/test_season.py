"""
test_season.py — winter (dormant) mode: season resolution, ingest/API guards,
cron skip, bot commands, and the dashboard render.

conftest.py pins storage.season to "summer" for every test; tests here either
use the real function (captured at import, before the fixture patches it) or
patch it to "winter".
"""

import pytest
from fastapi.testclient import TestClient

from garden import bot, main, storage
from garden.agent import runner
from garden.config import cfg

_real_season = storage.season

client = TestClient(main.app)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_db_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(storage, "season", _real_season)
    storage.init_db()
    return storage


@pytest.fixture
def winter(monkeypatch):
    monkeypatch.setattr(storage, "season", lambda: "winter")


# ── season resolution ─────────────────────────────────────────────────────────

class TestSeasonResolution:
    def test_config_default_when_no_override(self, db, monkeypatch):
        monkeypatch.setattr(cfg, "season_default", "winter")
        assert storage.season() == "winter"
        monkeypatch.setattr(cfg, "season_default", "summer")
        assert storage.season() == "summer"

    def test_override_beats_config(self, db, monkeypatch):
        monkeypatch.setattr(cfg, "season_default", "summer")
        storage.set_season("winter")
        assert storage.is_winter()

    def test_unknown_config_value_reads_as_summer(self, db, monkeypatch):
        monkeypatch.setattr(cfg, "season_default", "autumn")
        assert storage.season() == "summer"

    def test_set_season_rejects_unknown(self, db):
        with pytest.raises(ValueError):
            storage.set_season("spring")


# ── HTTP routes ───────────────────────────────────────────────────────────────

class TestRoutesInWinter:
    def test_ingest_acks_without_writing(self, winter, monkeypatch):
        writes = []
        monkeypatch.setattr(storage, "write_snapshot", lambda *a, **k: writes.append(a))
        resp = client.post("/api/ecowitt", data={"PASSKEY": cfg.ingest_passkey, "tempf": "40"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "dormant": True}
        assert writes == []

    def test_ingest_still_checks_passkey(self, winter):
        resp = client.post("/api/ecowitt", data={"PASSKEY": "wrong"})
        assert resp.status_code in (401, 403)

    def test_data_apis_are_empty(self, winter, monkeypatch):
        monkeypatch.setattr(storage, "latest", lambda: pytest.fail("read DB in winter"))
        assert client.get("/api/latest").json() == []
        assert client.get("/api/series?sensor=soilmoisture1").json() == []
        assert client.get("/api/insights").json() == {}

    def test_dashboard_renders_winter_page(self, winter, monkeypatch):
        monkeypatch.setattr(storage, "latest", lambda: pytest.fail("read DB in winter"))
        html = client.get("/").text
        assert 'data-season="winter"' in html
        assert "winter-bed-art" in html and "resting" in html
        assert "bed-chip-row" not in html
        assert "conn-dot" not in html
        assert 'SEASON:         "winter"' in html


# ── cron + brief ──────────────────────────────────────────────────────────────

class TestCronInWinter:
    def test_cron_tick_does_nothing(self, winter, monkeypatch):
        boom = lambda *a, **k: pytest.fail("ran in winter")  # noqa: E731
        monkeypatch.setattr(runner, "run_cron", boom)
        monkeypatch.setattr(runner, "_maybe_daily_brief", boom)
        monkeypatch.setattr(runner, "_maybe_prune_retention", boom)
        runner.run_cron_tick()

    def test_forced_brief_is_skipped(self, winter, monkeypatch):
        monkeypatch.setattr(runner, "tg", lambda *a, **k: pytest.fail("sent in winter"))
        runner.send_daily_brief(force=True)


# ── bot ───────────────────────────────────────────────────────────────────────

class TestBotSeasonCommands:
    def test_winter_then_summer_flip(self, db, monkeypatch):
        monkeypatch.setattr(cfg, "season_default", "summer")
        assert "Winter mode on" in bot.dispatch("winter")
        assert storage.is_winter()
        assert "Already in winter" in bot.dispatch("winter")
        assert "Summer mode on" in bot.dispatch("summer")
        assert not storage.is_winter()

    @pytest.mark.parametrize("command", ["bed1", "beds", "weather", "air", "brief"])
    def test_data_commands_are_dormant(self, winter, command):
        assert "dormant" in bot.dispatch(command)

    @pytest.mark.parametrize("command", ["help", "pause", "resume"])
    def test_control_commands_still_work(self, winter, monkeypatch, command):
        monkeypatch.setattr(storage, "pause_notifications", lambda *_: None)
        monkeypatch.setattr(storage, "resume_notifications", lambda: None)
        monkeypatch.setattr(storage, "notifications_paused_until", lambda: None)
        assert "dormant" not in bot.dispatch(command)

    def test_help_lists_season_commands(self):
        reply = bot.dispatch("help")
        assert "/winter" in reply and "/summer" in reply
