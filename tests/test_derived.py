"""
test_derived.py — Unit tests for garden.derived formulas and interpretation helpers.

All tests use known-value checks so failures pinpoint exactly which formula
regressed.  No I/O, no config, no DB — garden.derived is pure.
"""

import math
from datetime import date

import pytest

from garden.derived import (
    CROP_RANGES,
    analyze_watering,
    bed_moisture_band,
    bed_stress,
    bed_water_balance,
    days_until_dry,
    dew_point_f,
    drydown_rate,
    estimated_irrigation_in,
    et0_water_balance,
    etc_from_kc,
    frost_risk,
    gdd_base_for_bed,
    gdd_daily,
    gdd_growth_stage,
    heat_index_f,
    kc_for_crop,
    maturity_gdd_for_crop,
    project_harvest_date,
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


# ── bed_moisture_band ────────────────────────────────────────────────────────

class TestBedMoistureBand:
    def test_single_crop(self):
        assert bed_moisture_band(["tomato"]) == (50, 80)

    def test_intersection_across_crops(self):
        # okra (40,70) + peas (60,85) -> intersection (60,70)
        assert bed_moisture_band(["okra", "peas"]) == (60, 70)

    def test_unknown_plants_returns_none(self):
        assert bed_moisture_band(["foobar"]) is None

    def test_matches_bed_stress_thresholds(self):
        # bed_stress's dry/ok boundary should agree with bed_moisture_band's min
        moist_min, _ = bed_moisture_band(["tomato"])
        just_below = bed_stress(["tomato"], soil_moist=moist_min - 1, air_temp_f=75.0)
        just_at    = bed_stress(["tomato"], soil_moist=moist_min, air_temp_f=75.0)
        assert just_below["status"] == "dry"
        assert just_at["status"] == "ok"

    def test_custom_ranges(self):
        band = bed_moisture_band(["tomato"], custom_ranges={"tomato": {"moist": [70, 90]}})
        assert band == (70, 90)


# ── watering lifecycle: analyze_watering / drydown_rate / days_until_dry ──────

def _watering_series(baseline=62.0, peak=81.0, settled=71.0,
                      pre_pts=10, rise_pts=3, settle_pts=70,
                      interval=60.0, start_t=0.0, decay=6.0):
    """
    Synthetic (epoch_seconds, moisture_pct) watering curve: flat baseline,
    then a rise to `peak`, then an exponential fallback that plateaus at
    `settled`. settle_pts * interval defaults to 4200s (> the 3600s default
    settle window) so the plateau is fully captured (not "still settling").
    """
    samples = []
    t = start_t
    for _ in range(pre_pts):
        samples.append((t, baseline))
        t += interval
    for i in range(1, rise_pts + 1):
        v = baseline + (peak - baseline) * i / rise_pts
        samples.append((t, v))
        t += interval
    for i in range(1, settle_pts + 1):
        frac = i / settle_pts
        v = settled + (peak - settled) * math.exp(-decay * frac)
        samples.append((t, v))
        t += interval
    return samples


def _linear_series(start_val, per_day_rate, hours, interval_s=600.0, start_t=0.0):
    """(t, v) samples declining linearly at `per_day_rate` %/day over `hours`."""
    n = int(hours * 3600 / interval_s)
    samples = []
    for i in range(n + 1):
        t = start_t + i * interval_s
        v = start_val - per_day_rate * (t - start_t) / 86400.0
        samples.append((t, v))
    return samples


class TestAnalyzeWatering:
    def test_good_soak_known_values(self):
        samples = _watering_series(baseline=62.0, peak=81.0, settled=71.0)
        result = analyze_watering(samples)
        assert result["detected"] is True
        assert result["quality"] == "good_soak"
        assert abs(result["baseline"] - 62.0) < 0.5
        assert abs(result["peak"] - 81.0) < 0.5
        assert abs(result["settled"] - 71.0) < 1.0
        assert abs(result["absorbed"] - 9.0) < 1.5
        assert result["settling"] is False

    def test_runoff_drains_to_baseline(self):
        samples = _watering_series(baseline=62.0, peak=80.0, settled=63.0)
        result = analyze_watering(samples)
        assert result["detected"] is True
        assert result["quality"] == "runoff"
        assert "compact" in result["reason"].lower() or "ran off" in result["reason"].lower()

    def test_partial_soak(self):
        samples = _watering_series(baseline=60.0, peak=80.0, settled=63.5)
        result = analyze_watering(samples)
        assert result["detected"] is True
        assert result["quality"] == "partial"

    def test_no_event_flat(self):
        samples = [(i * 60.0, 55.0 + (0.4 if i % 2 == 0 else -0.4)) for i in range(20)]
        result = analyze_watering(samples)
        assert result["detected"] is False

    def test_no_event_gradual_drydown(self):
        samples = _linear_series(70.0, per_day_rate=3.0, hours=24)
        result = analyze_watering(samples)
        assert result["detected"] is False

    def test_too_few_points(self):
        result = analyze_watering([(0, 60.0), (60, 61.0), (120, 62.0)])
        assert result["detected"] is False

    def test_noise_robustness(self):
        base = _watering_series(baseline=62.0, peak=81.0, settled=71.0)
        jitter = [1.4, -1.3, 0.9, -1.1, 1.2, -0.8]
        noisy = [(t, v + jitter[i % len(jitter)]) for i, (t, v) in enumerate(base)]
        result = analyze_watering(noisy)
        assert result["detected"] is True
        assert result["quality"] == "good_soak"
        assert abs(result["settled"] - 71.0) < 2.5

    def test_baseline_is_median_not_skewed_by_outlier(self):
        samples = _watering_series(baseline=62.0, peak=81.0, settled=71.0, pre_pts=9)
        # Inject one wild low dip in the middle of the pre-water baseline region
        # (not at the very start, which would just become the rise's foot).
        samples[4] = (samples[4][0], 10.0)
        result = analyze_watering(samples)
        assert result["detected"] is True
        assert abs(result["baseline"] - 62.0) < 1.0

    def test_latest_event_wins(self):
        first  = _watering_series(baseline=40.0, peak=90.0, settled=60.0,
                                   settle_pts=20, start_t=0.0)
        gap_start = first[-1][0] + 60.0
        second = _watering_series(baseline=60.0, peak=75.0, settled=68.0,
                                   settle_pts=70, start_t=gap_start)
        result = analyze_watering(first + second)
        assert result["detected"] is True
        assert result["peak_ts"] == second[9 + 3][0]  # second event's peak sample
        assert abs(result["peak"] - 75.0) < 0.5


class TestDrydownRate:
    def test_linear_known_slope(self):
        samples = _linear_series(70.0, per_day_rate=3.2, hours=48)
        result = drydown_rate(samples)
        assert result["per_day"] == pytest.approx(3.2, abs=0.05)

    def test_per_hour_per_day_consistent(self):
        samples = _linear_series(70.0, per_day_rate=4.0, hours=48)
        result = drydown_rate(samples)
        assert result["per_day"] == pytest.approx(result["per_hour"] * 24, rel=0.01)

    def test_excludes_spike_and_fallback(self):
        watering = _watering_series(baseline=62.0, peak=81.0, settled=71.0)
        tail_start = watering[-1][0] + 60.0
        tail = _linear_series(71.0, per_day_rate=2.5, hours=24, start_t=tail_start)
        result = drydown_rate(watering + tail)
        # If the steep fallback contaminated the fit, this would be far > 2.5/day.
        assert result["per_day"] == pytest.approx(2.5, abs=0.3)

    def test_noisy_monotone_robust(self):
        base = _linear_series(70.0, per_day_rate=3.0, hours=48, interval_s=1800.0)
        jitter = [0.4, -0.3, 0.2, -0.5]
        noisy = [(t, v + jitter[i % len(jitter)]) for i, (t, v) in enumerate(base)]
        result = drydown_rate(noisy)
        assert result["per_day"] == pytest.approx(3.0, abs=0.4)

    def test_single_outlier_robust(self):
        base = _linear_series(70.0, per_day_rate=3.0, hours=48, interval_s=1800.0)
        base[10] = (base[10][0], base[10][1] + 30.0)  # one wild outlier
        result = drydown_rate(base)
        assert result["per_day"] == pytest.approx(3.0, abs=0.5)

    def test_rising_returns_zero(self):
        samples = [(i * 600.0, 40.0 + i * 0.5) for i in range(10)]  # steadily rising
        result = drydown_rate(samples)
        assert result["per_day"] == 0.0
        assert "rising" in result["reason"].lower()

    def test_too_few_points(self):
        result = drydown_rate([(0, 60.0), (600, 59.0)])
        assert result["per_day"] is None
        assert "few" in result["reason"].lower()

    def test_flat(self):
        samples = [(i * 600.0, 55.0) for i in range(10)]
        result = drydown_rate(samples)
        assert result["per_day"] == 0.0
        assert "flat" in result["reason"].lower()


class TestDaysUntilDry:
    def test_known_projection(self):
        result = days_until_dry(70.0, 4.0, 30.0)
        assert result["days"] == pytest.approx(10.0)
        assert result["label"] == "~10 days"

    def test_already_dry(self):
        result = days_until_dry(28.0, 4.0, 30.0)
        assert result["days"] == 0.0
        assert result["label"] == "today"

    def test_not_drying_zero_rate(self):
        result = days_until_dry(60.0, 0.0, 30.0)
        assert result["days"] is None
        assert result["label"] == "not drying"

    def test_not_drying_negative_rate(self):
        result = days_until_dry(60.0, -2.0, 30.0)
        assert result["days"] is None

    def test_sub_day_label(self):
        result = days_until_dry(50.0, 40.0, 30.0)
        assert result["days"] < 1.0
        assert result["label"] == "today"

    def test_far_horizon_clamp(self):
        result = days_until_dry(80.0, 0.5, 30.0)
        assert result["days"] > 14
        assert result["label"] == "2+ weeks"

    def test_label_pluralization(self):
        one = days_until_dry(34.0, 4.0, 30.0)
        two = days_until_dry(38.0, 4.0, 30.0)
        assert one["label"] == "~1 day"
        assert two["label"] == "~2 days"


# ── gdd_daily ─────────────────────────────────────────────────────────────────

class TestGddDaily:
    def test_known_value(self):
        # (90+60)/2 - 50 = 25.0
        assert gdd_daily(90.0, 60.0, 50.0) == pytest.approx(25.0)

    def test_entire_range_below_base(self):
        # Both tmax/tmin below base -> clamped to base -> 0 GDD
        assert gdd_daily(45.0, 30.0, 50.0) == pytest.approx(0.0)

    def test_low_only_below_base_clamped(self):
        # tmin clamped to base before averaging: (80+50)/2 - 50 = 15.0, not (80+30)/2-50=5.0
        assert gdd_daily(80.0, 30.0, 50.0) == pytest.approx(15.0)

    def test_never_negative(self):
        assert gdd_daily(20.0, 10.0, 50.0) >= 0.0


# ── gdd_base_for_bed ──────────────────────────────────────────────────────────

class TestGddBaseForBed:
    def test_picks_highest_base(self):
        # eggplant (50) + okra (55) -> okra's higher base wins for Tbase.
        # But eggplant takes far longer to mature (1300 vs okra's 900 GDD),
        # so eggplant -- not okra -- is the reference crop for growth-stage/
        # harvest-date reporting. See test_reference_crop_uses_longest_maturity.
        result = gdd_base_for_bed(["eggplant", "okra", "okra"])
        assert result == (55.0, "eggplant")

    def test_reference_crop_uses_longest_maturity_not_highest_base(self):
        # Tbase and reference crop can be different crops: okra sets the
        # (higher, more conservative) Tbase, but eggplant -- the slower,
        # "bottleneck" crop -- is the reference for stage/harvest reporting.
        result = gdd_base_for_bed(["eggplant", "okra"])
        assert result[0] == 55.0  # okra's Tbase, still the conservative max
        assert result[1] == "eggplant"  # eggplant's longer maturity wins reference-crop

    def test_variants_resolve_to_family_base(self):
        result = gdd_base_for_bed(["tomato_cherry", "tomato_roma"])
        assert result == (50.0, "tomato_cherry") or result == (50.0, "tomato_roma")
        assert result[0] == 50.0

    def test_no_recognised_crops(self):
        assert gdd_base_for_bed(["unknown_plant"]) is None

    def test_custom_base_override(self):
        result = gdd_base_for_bed(["eggplant", "okra"], custom_bases={"eggplant": 60.0})
        assert result == (60.0, "eggplant")


# ── gdd_growth_stage ──────────────────────────────────────────────────────────

class TestGddGrowthStage:
    def test_germination(self):
        result = gdd_growth_stage(50.0, "tomato")
        assert result["stage"] == "germination"
        assert result["gdd_into_stage"] == pytest.approx(50.0)
        assert result["gdd_to_next_stage"] == pytest.approx(40.0)

    def test_flowering(self):
        result = gdd_growth_stage(500.0, "tomato")
        assert result["stage"] == "flowering"
        assert result["gdd_into_stage"] == pytest.approx(100.0)
        assert result["gdd_to_next_stage"] == pytest.approx(200.0)
        assert result["pct_to_maturity"] == pytest.approx(500.0 / 1200 * 100, abs=0.1)

    def test_variant_resolves_to_family(self):
        result = gdd_growth_stage(500.0, "tomato_cherry")
        assert result["stage"] == "flowering"

    def test_at_maturity(self):
        result = gdd_growth_stage(1200.0, "tomato")
        assert result["stage"] == "maturity"
        assert result["gdd_to_next_stage"] is None
        assert result["pct_to_maturity"] == pytest.approx(100.0)

    def test_past_maturity(self):
        result = gdd_growth_stage(1500.0, "tomato")
        assert result["stage"] == "maturity"
        assert result["pct_to_maturity"] > 100.0

    def test_unrecognized_crop(self):
        result = gdd_growth_stage(500.0, "unknown_plant")
        assert result["stage"] == "unrecognized"
        assert result["pct_to_maturity"] is None


# ── project_harvest_date ──────────────────────────────────────────────────────

class TestProjectHarvestDate:
    def test_known_projection(self):
        result = project_harvest_date(700.0, 1200.0, 10.0, date(2026, 7, 8))
        assert result["days"] == pytest.approx(50.0)
        assert result["label"] == "~50 days"
        assert result["date"] == "2026-08-27"

    def test_already_mature(self):
        result = project_harvest_date(1300.0, 1200.0, 10.0, date(2026, 7, 8))
        assert result["days"] == 0.0
        assert result["label"] == "ready"
        assert result["date"] == "2026-07-08"

    def test_no_rate_data(self):
        result = project_harvest_date(500.0, 1200.0, None, date(2026, 7, 8))
        assert result["days"] is None
        assert result["label"] == "not enough data"

    def test_zero_rate(self):
        result = project_harvest_date(500.0, 1200.0, 0.0, date(2026, 7, 8))
        assert result["days"] is None

    def test_far_horizon_clamp(self):
        result = project_harvest_date(100.0, 1200.0, 5.0, date(2026, 7, 8))
        assert result["days"] >= 60
        assert result["label"] == "60+ days"
        assert result["date"] is None


# ── maturity_gdd_for_crop ─────────────────────────────────────────────────────

class TestMaturityGddForCrop:
    def test_family_key(self):
        assert maturity_gdd_for_crop("tomato") == 1200

    def test_variant_resolves_to_family(self):
        assert maturity_gdd_for_crop("sweet_pepper_yellow") == 1400

    def test_unrecognized_returns_none(self):
        assert maturity_gdd_for_crop("unknown_plant") is None


# ── kc_for_crop ───────────────────────────────────────────────────────────────

class TestKcForCrop:
    def test_family_key(self):
        assert kc_for_crop("tomato") == pytest.approx(1.15)

    def test_variant_resolves_to_family(self):
        assert kc_for_crop("tomato_cherry") == pytest.approx(1.15)
        assert kc_for_crop("sweet_pepper_red") == pytest.approx(1.05)

    def test_unrecognized_returns_none(self):
        assert kc_for_crop("unknown_plant") is None

    def test_custom_override(self):
        assert kc_for_crop("tomato", custom_kc={"tomato": 1.3}) == pytest.approx(1.3)


# ── etc_from_kc ───────────────────────────────────────────────────────────────

class TestEtcFromKc:
    def test_known_value(self):
        assert etc_from_kc(0.2, 1.15) == pytest.approx(0.23)

    def test_zero_et0(self):
        assert etc_from_kc(0.0, 1.15) == pytest.approx(0.0)


# ── estimated_irrigation_in ───────────────────────────────────────────────────

class TestEstimatedIrrigationIn:
    def test_known_value(self):
        # 20% absorbed, 9in root zone, 0.17 in/in AWC -> 0.306in
        assert estimated_irrigation_in(20.0, 9.0, 0.17) == pytest.approx(0.306)

    def test_zero_absorbed(self):
        assert estimated_irrigation_in(0.0, 9.0, 0.17) == pytest.approx(0.0)

    def test_negative_absorbed_clamped(self):
        assert estimated_irrigation_in(-5.0, 9.0, 0.17) == pytest.approx(0.0)


# ── bed_water_balance ─────────────────────────────────────────────────────────

class TestBedWaterBalance:
    def test_surplus(self):
        # 0.1 rain + 0.3 irrigation - 0.25 etc = 0.15 surplus
        assert bed_water_balance(0.1, 0.3, 0.25) == pytest.approx(0.15)

    def test_deficit(self):
        assert bed_water_balance(0.0, 0.0, 0.25) == pytest.approx(-0.25)

    def test_even(self):
        assert bed_water_balance(0.1, 0.0, 0.1) == pytest.approx(0.0)
