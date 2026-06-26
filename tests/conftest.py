"""
conftest.py — set dummy env vars before any garden.* import.

garden.config.cfg is a module-level singleton that calls _require() at import
time, raising RuntimeError if INGEST_PASSKEY / TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID are missing.  Setting them here (before the first import)
keeps tests hermetic — no secrets.env file needed.
"""

import os

os.environ.setdefault("INGEST_PASSKEY", "test-passkey")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test-bot-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "999999")
os.environ.setdefault("GARDEN_TIMEZONE", "America/Chicago")
os.environ.setdefault("DB_PATH", ":memory:")
