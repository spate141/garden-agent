"""
test_derived.py — Unit tests for garden.derived formulas and interpretation helpers.

All tests use known-value checks so failures pinpoint exactly which formula
regressed.  No I/O, no config, no DB — garden.derived is pure.
"""

import math

import pytest

from garden.derived import (
    CROP_RANGES,
    bed_stress,
    dew_point_f,
    et0_water_balance,
    frost_risk,
    heat_index_f,
    vpd_kpa,
    vpd_status,
)


# ── dew_point_f ───────────────────────────────────────────────────────────────

class TestDewPoint:
    def test_typical_summer(self):
        # 80°F / 60% RH → ~64.9°F dew point (Magnus formula reference)
        dp = dew_point_f(80.0, 60.0)
        assert abs(dp - 64.9) < 1.0, f"Expected ~64.9°F, got {dp:.2f}°F"

    def test_dry_desert(self):
        # 100°F / 10% RH → very low dew point (just above freezing ~33.7°F)
        dp = dew_point_f(100.0, 10.0)
        assert dp < 40.0, f"Expected a low dew point below 40°F, got {dp:.2f}°F"

    def test_saturated(self):
        # 70°F / 100% RH → dew point = air temp
        dp = dew_point_f(70.0, 100.0)
        assert abs(dp - 70.0) < 1.0, f"At 100% RH dew point ≈ air temp, got {dp:.2f}°F"

    def test_cold_night(self):
        # 40°F / 80% RH → dew point ~34.5°F (near frost)
        dp = dew_point_f(40.0, 80.0)
        assert 33.0 < dp < 37.0, f"Expected ~34–36°F, got {dp:.2f}°F"

    def test_not_above_air_temp(self):
        # Dew point can never exceed air temperature
        for temp in [32.0, 50.0, 75.0, 95.0]:
            for rh in [10.0, 50.0, 99.0]:
                dp = dew_point_f(temp, rh)
                assert dp <= temp + 0.1, f"Dew point {dp:.2f} > air temp {temp} at RH={rh}"


# ── vpd_kpa ───────────────────────────────────────────────────────────────────

class TestVpd:
    def test_zero_at_saturation(self):
        # At 100% RH, VPD must be 0
        assert vpd_kpa(70.0, 100.0) == pytest.approx(0.0, abs=0.01)

    def test_typical_grow_room(self):
        # 77°F / 60% RH → ~1.27 kPa (warm day, moderate humidity)
        v = vpd_kpa(77.0, 60.0)
        assert 0.8 < v < 1.5, f"Expected 0.8–1.5 kPa at 77°F/60%RH, got {v:.3f}"

    def test_heat_stress(self):
        # 95°F / 30% RH → high VPD (drought stress)
        v = vpd_kpa(95.0, 30.0)
        assert v > 2.0, f"Expected >2.0 kPa heat-stress VPD, got {v:.3f}"

    def test_always_non_negative(self):
        for temp in [40.0, 70.0, 100.0]:
            for rh in [1.0, 50.0, 99.0]:
                assert vpd_kpa(temp, rh) >= 0.0

    def test_increases_with_temperature(self):
        # Higher temp at same RH → higher VPD
        assert vpd_kpa(90.0, 50.0) > vpd_kpa(70.0, 50.0)

    def test_decreases_with_humidity(self):
        # Higher RH at same temp → lower VPD
        assert vpd_kpa(80.0, 80.0) < vpd_kpa(80.0, 40.0)


# ── heat_index_f ──────────────────────────────────────────────────────────────

class TestHeatIndex:
    def test_passthrough_below_80(self):
        # Below 80°F heat index is not amplified
        assert heat_index_f(75.0, 80.0) == 75.0

    def test_passthrough_low_humidity(self):
        # Below 40% RH heat index is not amplified
        assert heat_index_f(90.0, 35.0) == 90.0

    def test_amplified_hot_humid(self):
        # 90°F / 90% RH → feels significantly hotter
        hi = heat_index_f(90.0, 90.0)
        assert hi > 100.0, f"Expected >100°F feels-like, got {hi:.1f}°F"

    def test_known_nws_value(self):
        # NWS example: 96°F / 65% RH → ~121°F HI (famous "feels like" value)
        hi = heat_index_f(96.0, 65.0)
        assert abs(hi - 121.0) < 3.0, f"Expected ~121°F, got {hi:.1f}°F"

    def test_monotone_with_humidity(self):
        # At 85°F, higher humidity → higher heat index (above 40% RH)
        assert heat_index_f(85.0, 80.0) > heat_index_f(85.0, 50.0)


# ── et0_water_balance ─────────────────────────────────────────────────────────

class TestEt0WaterBalance:
    def test_deficit(self):
        # 0.1" rain, 0.25" ET₀ → -0.15" deficit
        wb = et0_water_balance(0.1, 0.25)
        assert abs(wb - (-0.15)) < 0.001

    def test_surplus(self):
        # 0.5" rain, 0.2" ET₀ → +0.3" surplus
        wb = et0_water_balance(0.5, 0.2)
        assert abs(wb - 0.3) < 0.001

    def test_zero_rain(self):
        # No rain → negative balance
        assert et0_water_balance(0.0, 0.18) < 0.0

    def test_zero_et0(self):
        # No evapotranspiration (cool cloudy day) → positive or zero
        assert et0_water_balance(0.0, 0.0) == pytest.approx(0.0)


# ── vpd_status ────────────────────────────────────────────────────────────────

class TestVpdStatus:
    def test_low(self):
        code, label = vpd_status(0.2)
        assert code == "low"
        assert "fungal" in label.lower() or "humid" in label.lower()

    def test_ok(self):
        code, label = vpd_status(0.8)
        assert code == "ok"

    def test_high(self):
        code, label = vpd_status(1.5)
        assert code == "high"

    def test_very_high(self):
        code, label = vpd_status(2.5)
        assert code == "very_high"

    def test_custom_thresholds(self):
        # Shift bands up — 1.0 kPa should now be "ok" with custom high=1.5
        code, _ = vpd_status(1.0, {"vpd_low": 0.5, "vpd_high": 1.5, "vpd_very_high": 2.5})
        assert code == "ok"


# ── frost_risk ────────────────────────────────────────────────────────────────

class TestFrostRisk:
    def test_frost_detected(self):
        is_risk, msg = frost_risk(34.0)
        assert is_risk is True
        assert "frost" in msg.lower()

    def test_no_frost_above_threshold(self):
        is_risk, msg = frost_risk(40.0)
        assert is_risk is False
        assert msg == ""

    def test_exactly_at_threshold(self):
        # ≤ threshold → risk
        is_risk, _ = frost_risk(35.6, frost_threshold_f=35.6)
        assert is_risk is True

    def test_custom_threshold(self):
        # Custom higher threshold
        is_risk, _ = frost_risk(38.0, frost_threshold_f=40.0)
        assert is_risk is True


# ── bed_stress ────────────────────────────────────────────────────────────────

class TestBedStress:
    def test_all_ok(self):
        result = bed_stress(["tomato"], soil_moist=65.0, air_temp_f=75.0)
        assert result["status"] == "ok"
        assert "Tomato" in result["crops"]

    def test_dry(self):
        result = bed_stress(["tomato"], soil_moist=30.0, air_temp_f=75.0)
        assert result["status"] == "dry"
        assert "dry" in result["reason"].lower()

    def test_wet(self):
        result = bed_stress(["tomato"], soil_moist=95.0, air_temp_f=75.0)
        assert result["status"] == "wet"

    def test_cold(self):
        result = bed_stress(["tomato"], soil_moist=65.0, air_temp_f=45.0)
        assert result["status"] == "cold"

    def test_heat(self):
        result = bed_stress(["tomato"], soil_moist=65.0, air_temp_f=100.0)
        assert result["status"] == "heat"

    def test_mixed_bed_okra_eggplant(self):
        # Bed 2 has eggplant (min 70°F) + okra (min 70°F)
        # At 65°F → cold for both
        result = bed_stress(["eggplant", "okra"], soil_moist=55.0, air_temp_f=65.0)
        assert result["status"] == "cold"

    def test_unknown_plants(self):
        result = bed_stress(["foobar", "baz"], soil_moist=50.0, air_temp_f=75.0)
        assert result["status"] == "unknown"

    def test_custom_ranges(self):
        # Override tomato min moisture to 70%
        result = bed_stress(
            ["tomato"],
            soil_moist=60.0,
            air_temp_f=75.0,
            custom_ranges={"tomato": {"moist": [70, 90], "temp": [60, 95]}},
        )
        assert result["status"] == "dry"

    def test_crop_ranges_complete(self):
        # All sprite names that can appear in config.yaml should be in CROP_RANGES
        for crop in ["tomato", "eggplant", "okra", "peas", "sweet_pepper", "hot_pepper", "zucchini"]:
            assert crop in CROP_RANGES, f"Missing crop: {crop}"
