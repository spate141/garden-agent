"""
test_rules.py — Unit tests for the per-bed self-learned dry threshold in
garden.agent.rules.check_soil_moisture_low().

Background: a capacitive soil sensor reads soil dielectric, so loose/fresh
soil reads a lower % than compacted soil at the same plant-available water.
A single flat "below 30%" threshold for every bed can leave a loose-soil bed
permanently un-alerted (or a compacted bed alerted too late/early). These
tests lock in that the alert now uses each bed's self-learned band (falling
back to the crop band, then the flat config threshold) — the same logic the
dashboard chip uses — so the Telegram alert agrees with what's on screen.
"""

from unittest.mock import patch

from garden import storage
from garden.agent.rules import _bed_dry_threshold, check_soil_moisture_low
from garden.config import cfg


def _sawtooth(low: float, high: float, cycles: int = 12, points_per_cycle: int = 30) -> list[float]:
    vals: list[float] = []
    for _ in range(cycles):
        vals += [high - (high - low) * i / (points_per_cycle - 1) for i in range(points_per_cycle)]
    return vals


BED1_LOOSE = {
    "id": "bed1",
    "name": "Bed 1",
    "sensors": {"soil_moisture": "soilmoisture1"},
    "plants": ["tomato_cherry"],
}


class TestBedDryThreshold:
    def test_falls_back_to_flat_threshold_without_history(self):
        with patch.object(storage, "series", return_value=[]):
            threshold = _bed_dry_threshold(BED1_LOOSE, fallback=30.0)
        # No history and no recognised-crop band mismatch here: falls back to
        # the crop-derived band (tomato min 50), not the flat fallback,
        # because bed_moisture_band still resolves without any samples.
        assert threshold == 50.0

    def test_learns_a_lower_threshold_for_loose_soil(self):
        series_rows = [{"value": v} for v in _sawtooth(35, 58)]
        with patch.object(storage, "series", return_value=series_rows):
            threshold = _bed_dry_threshold(BED1_LOOSE, fallback=30.0)
        # Should NOT be the flat crop-band floor (50) that permanently flags
        # this bed dry — it should reflect the bed's own lower envelope.
        assert threshold < 50.0
        assert 34.0 <= threshold < 58.0

    def test_disabled_learning_uses_crop_band(self):
        series_rows = [{"value": v} for v in _sawtooth(35, 58)]
        original = cfg.thresholds.get("moisture_learning", {}).copy()
        cfg.thresholds["moisture_learning"] = {**original, "enabled": False}
        try:
            with patch.object(storage, "series", return_value=series_rows):
                threshold = _bed_dry_threshold(BED1_LOOSE, fallback=30.0)
        finally:
            cfg.thresholds["moisture_learning"] = original
        assert threshold == 50.0  # crop band min, learning bypassed


class TestCheckSoilMoistureLow:
    def test_fires_using_learned_threshold_not_flat_30(self):
        # Readings sit at 40% — above the flat 30% alert floor, so the OLD
        # behavior would never fire. But 40% is dry for THIS bed's own
        # learned envelope (~35-58%, learned min ~43%), so the alert should
        # now fire.
        original_beds = cfg.dashboard.get("beds")
        cfg.dashboard["beds"] = [BED1_LOOSE]
        original_thresholds = cfg.thresholds.get("soil_moisture_low", {}).copy()
        cfg.thresholds["soil_moisture_low"] = {
            **original_thresholds,
            "sensor_keys": ["soilmoisture1"],
            "below": 30,
            "consecutive": 3,
        }
        series_rows = [{"value": v} for v in _sawtooth(35, 58)]
        try:
            with patch.object(storage, "series", return_value=series_rows), \
                 patch.object(storage, "recent_values", return_value=[40.0, 40.0, 40.0]):
                results = check_soil_moisture_low()
        finally:
            cfg.dashboard["beds"] = original_beds
            cfg.thresholds["soil_moisture_low"] = original_thresholds

        assert len(results) == 1
        assert results[0].fired is True
        assert "Time to water" in results[0].body

    def test_does_not_fire_when_bed_unrecognized_uses_flat_fallback(self):
        # Bed not present in cfg.dashboard.beds -> flat fallback (30%) applies,
        # matching pre-existing behavior for sensors with no bed mapping.
        original_beds = cfg.dashboard.get("beds")
        cfg.dashboard["beds"] = []
        original_thresholds = cfg.thresholds.get("soil_moisture_low", {}).copy()
        cfg.thresholds["soil_moisture_low"] = {
            **original_thresholds,
            "sensor_keys": ["soilmoisture1"],
            "below": 30,
            "consecutive": 3,
        }
        try:
            with patch.object(storage, "recent_values", return_value=[45.0, 45.0, 45.0]):
                results = check_soil_moisture_low()
        finally:
            cfg.dashboard["beds"] = original_beds
            cfg.thresholds["soil_moisture_low"] = original_thresholds

        assert results[0].fired is False
