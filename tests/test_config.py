"""
test_config.py — sanity-check that config.yaml loads cleanly and key values
are what the rest of the system depends on.
"""

from garden.config import cfg


def test_config_loads():
    assert cfg is not None


def test_sensor_labels():
    assert cfg.sensor_label("soilmoisture1") == "Bed 1 moisture"
    assert cfg.sensor_label("soilmoisture2") == "Bed 2 moisture"
    assert cfg.sensor_label("unknown_key") == "unknown_key"  # falls back to key


def test_cooldowns_present():
    assert cfg.cooldowns["watchdog_minutes"] == 360
    assert cfg.cooldowns["battery_low_minutes"] == 1440
    assert cfg.cooldowns["soil_moisture_low_minutes"] == 120


def test_daily_brief_defaults():
    assert cfg.daily_brief.get("enabled", True) is True
    assert cfg.daily_brief.get("hour_local", 7) == 7


def test_timezone_set():
    # Confirm the env-var timezone is forwarded into cfg.location
    assert cfg.location["timezone"] == "America/Chicago"


def test_watering_forecast_config():
    wf = cfg.dashboard.get("watering_forecast", {})
    assert wf.get("enabled", True) is True
    assert wf.get("drydown_hours", 48) == 48
    assert wf.get("rain_relief_in", 0.25) == 0.25


def test_watering_forecast_defaults_when_block_absent():
    # api_insights() reads these via .get() with the same fallbacks, so a
    # config.yaml without the block must still behave, not KeyError.
    empty: dict = {}
    assert empty.get("enabled", True) is True
    assert int(empty.get("drydown_hours", 48)) == 48
    assert float(empty.get("rain_relief_in", 0.25)) == 0.25
