"""
derived.py — Derived agronomic metrics computed from raw sensor readings.

All functions are pure (no I/O, no config imports) for easy unit testing.

Public API:
  dew_point_f(temp_f, rh)          → float (°F)
  vpd_kpa(temp_f, rh)              → float (kPa)
  heat_index_f(temp_f, rh)         → float (°F) — same as temp_f below 80°F/40%RH
  et0_water_balance(precip, et0)   → float (inches, positive = surplus)
  vpd_status(vpd, thresholds)      → (status_code, human_label)
  frost_risk(dewpoint_f, threshold) → (bool, message)
  bed_stress(plants, moist, temp)  → {status, reason, crops}
  bed_moisture_band(plants)        → (min%, max%) | None
  family_labels(plants)            → list of unique lowercase crop-family names
  analyze_watering(samples)        → {detected, baseline, peak, settled, quality, ...}
  drydown_rate(samples)            → {per_day, per_hour, n_points, reason}
  days_until_dry(moist, rate, dry_threshold) → {days, label}
  gdd_daily(tmax_f, tmin_f, base_f)          → float (°F-days, never negative)
  gdd_base_for_bed(plants)                   → (base_f, reference_crop) | None
  gdd_growth_stage(cumulative_gdd, crop_key) → {stage, pct_to_maturity, ...}
  project_harvest_date(cum_gdd, maturity, avg_rate, today) → {days, date, label}
  etc_from_kc(et0_in, kc)                    → float (inches) — ETc = ET0 x Kc
  estimated_irrigation_in(absorbed_pct, root_zone_in, awc) → float (inches, modeled estimate)
  bed_water_balance(rain, irrigation, etc)   → float (inches, positive = surplus)

CROP_RANGES — default ideal soil-moisture/temp ranges per vegetable type.
GDD_BASE_F / GDD_STAGES / KC_MID — GDD base temps, growth-stage breakpoints,
  and crop coefficients per vegetable type (see the GDD section below).

Watering-lifecycle functions (analyze_watering/drydown_rate/days_until_dry) take
samples as list[tuple[float, float]] of (epoch_seconds, moisture_pct), oldest→newest.
Always feed them a NARROW window (event ≤2h, drydown ≤48h) — storage.series()
bucket-averages wide windows down to ~350 points, which smears a multi-minute
watering spike and biases these estimates.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any


# ── Unit helpers ──────────────────────────────────────────────────────────────

def _tc(temp_f: float) -> float:
    """Fahrenheit → Celsius."""
    return (temp_f - 32.0) * 5.0 / 9.0


def _tf(temp_c: float) -> float:
    """Celsius → Fahrenheit."""
    return temp_c * 9.0 / 5.0 + 32.0


# ── Atmospheric formulas ──────────────────────────────────────────────────────

def dew_point_f(temp_f: float, rh: float) -> float:
    """
    Magnus-formula dew point in °F.

    rh: relative humidity 0-100.
    Accurate to ±0.4°C over 0-60°C / 1-100% RH.
    """
    a, b = 17.27, 237.3
    Tc = _tc(temp_f)
    # Clamp RH to avoid log(0); real sensors never hit 0% RH
    gamma = (a * Tc) / (b + Tc) + math.log(max(rh, 0.1) / 100.0)
    Td_c = (b * gamma) / (a - gamma)
    return _tf(Td_c)


def vpd_kpa(temp_f: float, rh: float) -> float:
    """
    Vapour Pressure Deficit in kPa.

    VPD = es × (1 − rh/100)
    Saturation vapour pressure (Tetens / Magnus):
      es = 0.6108 × exp(17.27 × Tc / (Tc + 237.3))

    Healthy plant range: ~0.4–1.2 kPa.
    Above 1.6 kPa plants begin to close stomata; above 2.0 kPa heat stress.
    """
    Tc = _tc(temp_f)
    es = 0.6108 * math.exp((17.27 * Tc) / (Tc + 237.3))
    return es * max(1.0 - rh / 100.0, 0.0)


def heat_index_f(temp_f: float, rh: float) -> float:
    """
    NWS Rothfusz regression apparent temperature in °F.

    Returns temp_f unchanged below 80 °F or below 40 % RH — the regression
    is only defined and meaningful in the hot-humid regime.
    """
    if temp_f < 80.0 or rh < 40.0:
        return temp_f
    T, R = temp_f, rh
    hi = (
        -42.379
        + 2.04901523 * T
        + 10.14333127 * R
        - 0.22475541 * T * R
        - 0.00683783 * T * T
        - 0.05481717 * R * R
        + 0.00122874 * T * T * R
        + 0.00085282 * T * R * R
        - 0.00000199 * T * T * R * R
    )
    # NWS low-humidity adjustment (RH<13, 80≤T≤112)
    if rh < 13.0 and 80.0 <= temp_f <= 112.0:
        adj = ((13.0 - rh) / 4.0) * math.sqrt((17.0 - abs(temp_f - 95.0)) / 17.0)
        hi -= adj
    # NWS high-humidity adjustment (RH>85, 80≤T≤87)
    elif rh > 85.0 and 80.0 <= temp_f <= 87.0:
        adj = ((rh - 85.0) / 10.0) * ((87.0 - temp_f) / 5.0)
        hi += adj
    return hi


def et0_water_balance(precip_in: float, et0_in: float) -> float:
    """
    Net daily water balance in inches.

    Positive = moisture surplus (rain > ET₀, skip irrigation).
    Negative = moisture deficit (ET₀ > rain, plants need water).

    et0_in: reference evapotranspiration; from Open-Meteo field
            et0_fao_evapotranspiration (returned in mm — convert: mm / 25.4).
    """
    return precip_in - et0_in


# ── Interpretation helpers ────────────────────────────────────────────────────

def vpd_status(vpd: float, thresholds: dict[str, float] | None = None) -> tuple[str, str]:
    """
    Classify a VPD value into a status code + human label.

    status codes: 'low' | 'ok' | 'high' | 'very_high'

    Default band edges (configurable via config.yaml derived.thresholds):
      < 0.4  kPa → low      (high humidity, slow transpiration, fungal risk)
      0.4–1.2 kPa → ok       (healthy transpiration)
      1.2–2.0 kPa → high     (transpiring fast, watch moisture)
      > 2.0  kPa → very_high (heat/drought stress)
    """
    t = thresholds or {}
    vpd_low      = t.get("vpd_low", 0.4)
    vpd_high     = t.get("vpd_high", 1.2)
    vpd_very_high = t.get("vpd_very_high", 2.0)

    if vpd < vpd_low:
        return "low", "Humid air, slow transpiration, watch for fungal disease"
    if vpd <= vpd_high:
        return "ok", "Plants transpiring at a healthy rate"
    if vpd <= vpd_very_high:
        return "high", "Plants transpiring fast, keep moisture up"
    return "very_high", "Heat/drought stress risk, water soon"


def frost_risk(dewpoint_f_val: float, frost_threshold_f: float = 35.6) -> tuple[bool, str]:
    """
    Dew point ≤ frost_threshold_f is the strongest predictor of a killing frost.
    Air temperature rarely drops below the dew point, so this method reliably
    flags nights when frost is possible before temps have actually reached freezing.

    Returns (is_risk: bool, message: str).
    """
    if dewpoint_f_val <= frost_threshold_f:
        return (
            True,
            f"Frost risk: dew point {dewpoint_f_val:.1f}°F, protect tender plants",
        )
    return False, ""


# ── Crop ranges + bed stress ──────────────────────────────────────────────────

# Default ideal ranges per crop.
# moist: (min%, max%) soil moisture from WH51; temp: (min°F, max°F) air temperature.
# Values are conservative growing-season averages; override via config.yaml crops: block.
CROP_RANGES: dict[str, dict[str, Any]] = {
    "tomato":       {"moist": (50, 80), "temp": (60, 95),  "label": "Tomato"},
    "tomato_cherry":      {"moist": (50, 80), "temp": (60, 95), "label": "Cherry Tomato"},
    "tomato_roma":        {"moist": (50, 80), "temp": (60, 95), "label": "Roma Tomato"},
    "tomato_beefsteak":   {"moist": (50, 80), "temp": (60, 95), "label": "Beefsteak Tomato"},
    "tomato_heirloom":    {"moist": (50, 80), "temp": (60, 95), "label": "Heirloom Tomato"},
    "tomato_grape":       {"moist": (50, 80), "temp": (60, 95), "label": "Grape Tomato"},
    "tomato_san_marzano": {"moist": (50, 80), "temp": (60, 95), "label": "San Marzano Tomato"},
    "eggplant":     {"moist": (50, 75), "temp": (65, 95),  "label": "Eggplant"},
    "okra":         {"moist": (40, 70), "temp": (70, 100), "label": "Okra"},
    "peas":         {"moist": (60, 85), "temp": (45, 75),  "label": "Peas"},
    "sweet_pepper": {"moist": (50, 75), "temp": (65, 90),  "label": "Sweet Pepper"},
    "sweet_pepper_red":    {"moist": (50, 75), "temp": (65, 90), "label": "Red Sweet Pepper"},
    "sweet_pepper_green":  {"moist": (50, 75), "temp": (65, 90), "label": "Green Sweet Pepper"},
    "sweet_pepper_yellow": {"moist": (50, 75), "temp": (65, 90), "label": "Yellow Sweet Pepper"},
    "sweet_pepper_orange": {"moist": (50, 75), "temp": (65, 90), "label": "Orange Sweet Pepper"},
    "hot_pepper":   {"moist": (45, 70), "temp": (65, 95),  "label": "Hot Pepper"},
    "zucchini":     {"moist": (55, 80), "temp": (60, 90),  "label": "Zucchini"},
}


# ── GDD (Growing Degree Day) reference data ───────────────────────────────────

# Base temperature (Tbase, °F) below which a crop accrues no growth for the
# day. Standard agronomic consensus values (NOAA/university-extension GDD
# guides), one entry per CROP_RANGES key — variants share their family's
# Tbase since base temperature doesn't vary by fruit color/variety:
#   - Warm-season fruiting crops (tomato / eggplant / sweet & hot pepper): 50°F
#   - Okra / zucchini (higher heat requirement): 55°F
#   - Peas (cool-season legume): 40°F
GDD_BASE_F: dict[str, float] = {
    "tomato":             50.0,
    "tomato_cherry":      50.0,
    "tomato_roma":        50.0,
    "tomato_beefsteak":   50.0,
    "tomato_heirloom":    50.0,
    "tomato_grape":       50.0,
    "tomato_san_marzano": 50.0,
    "eggplant":     50.0,
    "okra":         55.0,
    "peas":         40.0,
    "sweet_pepper":        50.0,
    "sweet_pepper_red":    50.0,
    "sweet_pepper_green":  50.0,
    "sweet_pepper_yellow": 50.0,
    "sweet_pepper_orange": 50.0,
    "hot_pepper":   50.0,
    "zucchini":     55.0,
}

# Cumulative-GDD breakpoints (°F-days, base per GDD_BASE_F) marking the START
# of each growth stage; "maturity" is the first-harvest target. One entry per
# crop FAMILY (not variant, unlike CROP_RANGES/GDD_BASE_F) — stage-timing
# research doesn't distinguish tomato colors. Sourced from typical extension-
# service GDD-to-maturity tables; treat as rough midpoints for common
# varieties, not variety-specific data — same "good enough, documented"
# spirit as heat_index_f's regression validity bounds.
GDD_STAGES: dict[str, dict[str, float]] = {
    "tomato":       {"germination": 0, "vegetative": 90,  "flowering": 400, "fruiting": 700, "maturity": 1200},
    "eggplant":     {"germination": 0, "vegetative": 100, "flowering": 450, "fruiting": 750, "maturity": 1300},
    "okra":         {"germination": 0, "vegetative": 80,  "flowering": 350, "fruiting": 550, "maturity": 900},
    "peas":         {"germination": 0, "vegetative": 60,  "flowering": 250, "fruiting": 400, "maturity": 600},
    "sweet_pepper": {"germination": 0, "vegetative": 110, "flowering": 500, "fruiting": 800, "maturity": 1400},
    "hot_pepper":   {"germination": 0, "vegetative": 110, "flowering": 500, "fruiting": 800, "maturity": 1500},
    "zucchini":     {"germination": 0, "vegetative": 60,  "flowering": 200, "fruiting": 350, "maturity": 550},
}
_GDD_STAGE_ORDER = ("germination", "vegetative", "flowering", "fruiting", "maturity")

# Flat FAO-56 mid-season crop coefficient (Kc) per crop family — a single
# average value rather than staged Kc-ini/Kc-mid/Kc-late. Proportionate for a
# home dashboard: slightly over-estimates ETc during germination and under-
# estimates during late senescence, but avoids a full dual-crop-coefficient
# model. (Staging Kc by gdd_growth_stage()'s result is a cheap v2 if needed.)
KC_MID: dict[str, float] = {
    "tomato":       1.15,
    "eggplant":     1.05,
    "okra":         1.05,
    "peas":         1.15,
    "sweet_pepper": 1.05,
    "hot_pepper":   1.05,
    "zucchini":     1.00,
}


def _gdd_family(crop_key: str) -> str | None:
    """
    Resolve a crop variant (e.g. 'tomato_cherry', 'sweet_pepper_red') to its
    GDD_STAGES/KC_MID reference family key (e.g. 'tomato', 'sweet_pepper').
    Returns None for unrecognised keys.
    """
    if crop_key in GDD_STAGES:
        return crop_key
    for fam in sorted(GDD_STAGES, key=len, reverse=True):
        if crop_key.startswith(fam + "_"):
            return fam
    return None


def family_labels(plants: list[str]) -> list[str]:
    """
    Collapse a bed's plant list to unique lowercase crop-family labels, order preserved.

    e.g. ['tomato_cherry', 'tomato_roma'] -> ['tomato']
         ['zucchini', 'eggplant']         -> ['zucchini', 'eggplant']

    Used to keep Telegram alert titles short — varieties (cherry/roma/heirloom...)
    collapse to their shared family (tomato) instead of listing each one.
    """
    keys_by_len = sorted(CROP_RANGES, key=len)  # shortest key wins → family, not variant
    out: list[str] = []
    seen: set[str] = set()
    for p in plants:
        base = next((k for k in keys_by_len if p == k or p.startswith(k + "_")), None)
        if base is None:
            continue
        label = CROP_RANGES[base]["label"].lower()
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _merge_crop_ranges(custom_ranges: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Default CROP_RANGES with config.yaml crops: overrides layered on top."""
    ranges: dict[str, dict[str, Any]] = {}
    for key, defaults in CROP_RANGES.items():
        ranges[key] = dict(defaults)
    if custom_ranges:
        for crop, overrides in custom_ranges.items():
            if crop in ranges and isinstance(overrides, dict):
                ranges[crop] = {**ranges[crop], **overrides}
    return ranges


def _unique_recognized(plants: list[str], ranges: dict[str, dict[str, Any]]) -> list[str]:
    """Unique recognised plant keys from a bed's plant list, order preserved."""
    seen: set[str] = set()
    unique: list[str] = []
    for p in plants:
        if p in ranges and p not in seen:
            unique.append(p)
            seen.add(p)
    return unique


def bed_moisture_band(
    plants: list[str],
    custom_ranges: dict[str, Any] | None = None,
) -> tuple[float, float] | None:
    """
    Crop-derived (min%, max%) soil-moisture band for a bed, aggregated by
    intersection across crops (most conservative — same logic bed_stress uses).

    Returns None when no recognised plants. This is the single source of truth
    for a bed's dry/wet thresholds, shared by bed_stress and the watering-lifecycle
    forecast (days_until_dry) so both agree on what "dry" means for a given bed.
    """
    ranges = _merge_crop_ranges(custom_ranges)
    unique = _unique_recognized(plants, ranges)
    if not unique:
        return None
    moist_min = max(ranges[p]["moist"][0] for p in unique)
    moist_max = min(ranges[p]["moist"][1] for p in unique)
    return (moist_min, moist_max)


def bed_stress(
    plants: list[str],
    soil_moist: float,
    air_temp_f: float,
    custom_ranges: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Assess the stress state of a raised bed.

    plants:        crop keys (e.g. ['tomato', 'tomato', 'tomato']).
    soil_moist:    current soil moisture % from the bed's WH51 sensor.
    air_temp_f:    outdoor air temperature °F.
    custom_ranges: optional per-crop overrides from config.yaml crops: block
                   (each value may contain 'moist' and/or 'temp' lists).

    When a bed holds multiple crop types, we use the intersection of their ideal
    ranges (most conservative), since they share one soil sensor.

    Returns:
      {
        "status":  "ok" | "dry" | "wet" | "cold" | "heat" | "unknown",
        "reason":  human-readable string,
        "crops":   list of unique crop label strings in this bed,
      }
    """
    ranges = _merge_crop_ranges(custom_ranges)
    unique = _unique_recognized(plants, ranges)

    if not unique:
        return {
            "status": "unknown",
            "reason": "No recognised crop types in bed",
            "detail": "No recognised crop types in bed",
            "crops": [],
        }

    # Aggregate range: intersection (strictest min and max across crops)
    moist_min, moist_max = bed_moisture_band(plants, custom_ranges)
    temp_min  = max(ranges[p]["temp"][0] for p in unique)
    temp_max  = min(ranges[p]["temp"][1] for p in unique)
    labels    = [ranges[p]["label"] for p in unique]
    label_str = ", ".join(labels)

    # Temperature stress takes priority over moisture stress.
    # "reason" includes crop names (for the LLM brief); "detail" omits them
    # (for the compact dashboard insight card, which shows the bed name already).
    if air_temp_f < temp_min:
        return {
            "status": "cold",
            "reason": f"Too cold for {label_str}: {air_temp_f:.0f}°F, min {temp_min:.0f}°F",
            "detail": f"Too cold: {air_temp_f:.0f}°F, min {temp_min:.0f}°F",
            "crops": labels,
        }
    if air_temp_f > temp_max:
        return {
            "status": "heat",
            "reason": f"Heat stress for {label_str}: {air_temp_f:.0f}°F, max {temp_max:.0f}°F",
            "detail": f"Heat stress: {air_temp_f:.0f}°F, max {temp_max:.0f}°F",
            "crops": labels,
        }
    if soil_moist < moist_min:
        return {
            "status": "dry",
            "reason": f"Soil too dry for {label_str}: {soil_moist:.0f}%, min {moist_min:.0f}%",
            "detail": f"Soil too dry: {soil_moist:.0f}%, min {moist_min:.0f}%",
            "crops": labels,
        }
    if soil_moist > moist_max:
        return {
            "status": "wet",
            "reason": f"Soil too wet for {label_str}: {soil_moist:.0f}%, max {moist_max:.0f}%",
            "detail": f"Soil too wet: {soil_moist:.0f}%, max {moist_max:.0f}%",
            "crops": labels,
        }
    return {
        "status": "ok",
        "reason": f"{label_str}: moisture {soil_moist:.0f}%, temp {air_temp_f:.0f}°F, all good",
        "detail": f"Moisture {soil_moist:.0f}%, temp {air_temp_f:.0f}°F, all good",
        "crops": labels,
    }


# ── Watering lifecycle ──────────────────────────────────────────────────────────
#
# A watered bed traces a sawtooth curve: SPIKE (water floods the sensor) →
# RAPID FALLBACK (gravitational/excess water drains to field capacity, minutes
# to ~1h) → GRADUAL DRYDOWN (evapotranspiration + uptake, hours to days, until
# the bed needs watering again). analyze_watering() characterizes the first two
# phases; drydown_rate()/days_until_dry() characterize and project the third.

def _find_latest_rise_event(
    times: list[float],
    values: list[float],
    min_rise: float,
) -> tuple[int, int] | None:
    """
    Decompose the series into alternating falling/rising runs and return the
    (foot_index, peak_index) of the MOST RECENT run whose rise >= min_rise.

    Scanning for local min→max runs (rather than the single global maximum)
    is what lets this find the latest watering event even when an earlier
    spike in the window was taller.
    """
    n = len(values)
    events: list[tuple[int, int]] = []
    i = 0
    while i < n - 1:
        foot_i = i
        while foot_i < n - 1 and values[foot_i + 1] <= values[foot_i]:
            foot_i += 1
        peak_i = foot_i
        while peak_i < n - 1 and values[peak_i + 1] >= values[peak_i]:
            peak_i += 1
        if peak_i > foot_i and (values[peak_i] - values[foot_i]) >= min_rise:
            events.append((foot_i, peak_i))
        i = max(peak_i, foot_i + 1)
    return events[-1] if events else None


def analyze_watering(
    samples: list[tuple[float, float]],
    min_rise: float = 10.0,
    settle_window_s: float = 3600,
    noise_pct: float = 2.0,
) -> dict[str, Any]:
    """
    Detect and characterize the most recent watering event in `samples`.

    samples: list[(epoch_seconds, moisture_pct)], oldest → newest. Feed a
             narrow window (<=2h) — see module docstring on bucket smearing.
    min_rise: minimum peak-foot rise (%) to count as a watering event.
    settle_window_s: how long after the peak to look for the post-fallback
                      plateau (field capacity).
    noise_pct: unused directly (baseline/settled use medians, which are
               robust to sensor noise of roughly this magnitude by
               construction) — kept as a documented tuning knob for callers.

    Returns, when no event is found:
      {"detected": False, "reason": "..."}
    Returns, when an event is found:
      {
        "detected":  True,
        "baseline":  pre-water level (median, °% moisture),
        "peak":      spike top (%),
        "settled":   post-fallback plateau / field capacity (%),
        "absorbed":  settled - baseline (net retained gain, %),
        "overshoot": peak - settled (drained gravitational water + sensor overshoot, %),
        "quality":   "good_soak" | "partial" | "runoff",
        "peak_ts":   epoch seconds of the peak,
        "settling":  True if the window ends before the plateau is confirmed,
        "reason":    human-readable summary,
      }
    """
    if len(samples) < 4:
        return {"detected": False, "reason": "too few samples"}

    times  = [s[0] for s in samples]
    values = [s[1] for s in samples]

    event = _find_latest_rise_event(times, values, min_rise)
    if event is None:
        return {"detected": False, "reason": "no watering event in window"}
    foot_i, peak_i = event

    peak    = values[peak_i]
    peak_ts = times[peak_i]

    # Baseline: median of the ~15 min of samples immediately before the foot —
    # robust to a single noisy/outlier pre-water reading.
    baseline_window_s = 900
    baseline_vals = [
        values[i] for i in range(foot_i + 1)
        if times[foot_i] - times[i] <= baseline_window_s
    ]
    baseline = statistics.median(baseline_vals) if baseline_vals else values[foot_i]

    # Settle window: samples within settle_window_s after the peak.
    settle_idxs = [i for i in range(peak_i, len(values)) if times[i] - peak_ts <= settle_window_s]
    if not settle_idxs:
        settle_idxs = [peak_i]
    settling = (times[-1] - peak_ts) < settle_window_s

    # Settled / field capacity: median of the last third of the settle window
    # (the plateau after the steep initial drainage) — robust to noise and to
    # exactly where the fallback "finishes."
    k = max(1, len(settle_idxs) // 3)
    tail_idxs = settle_idxs[-k:]
    settled = statistics.median([values[i] for i in tail_idxs])

    absorbed  = settled - baseline
    overshoot = peak - settled

    good_soak_min_pct  = 5.0
    runoff_max_pct     = 2.0
    overshoot_frac_max = 0.6
    span = peak - baseline
    overshoot_frac = (overshoot / span) if span > 0 else 0.0

    if absorbed <= runoff_max_pct:
        quality = "runoff"
        reason = f"Mostly ran off ({baseline:.0f}→{settled:.0f}%) — soil may be compacted or hydrophobic"
    elif absorbed >= good_soak_min_pct and overshoot_frac <= overshoot_frac_max:
        quality = "good_soak"
        reason = f"Absorbed +{absorbed:.0f}% ({baseline:.0f}→{settled:.0f}%), good soak"
    else:
        quality = "partial"
        reason = f"Partial soak, absorbed +{absorbed:.0f}% ({baseline:.0f}→{settled:.0f}%)"

    if settling:
        reason += " (still settling)"

    return {
        "detected": True,
        "baseline": baseline,
        "peak": peak,
        "settled": settled,
        "absorbed": absorbed,
        "overshoot": overshoot,
        "quality": quality,
        "peak_ts": peak_ts,
        "settling": settling,
        "reason": reason,
    }


def drydown_rate(
    samples: list[tuple[float, float]],
    settle_window_s: float = 3600,
    min_points: int = 4,
) -> dict[str, Any]:
    """
    Robust rate of the gradual-drydown phase, excluding any watering spike
    and its post-fallback settling.

    samples: list[(epoch_seconds, moisture_pct)], oldest → newest. Feed a
             lookback window of hours-to-a-couple-days — see module docstring
             on bucket smearing.

    Uses the Theil-Sen estimator (median of all pairwise slopes) rather than a
    single least-squares fit, so a handful of noisy points or one outlier
    reading don't swing the result.

    Returns:
      {"per_day": float | None, "per_hour": float | None, "n_points": int, "reason": str}
    per_day/per_hour are positive magnitudes (rate of drying). None when there
    aren't enough points to fit; 0.0 when the tail is flat or net-rising
    (e.g. still within/near a watering event).
    """
    if len(samples) < min_points:
        return {"per_day": None, "per_hour": None, "n_points": len(samples), "reason": "too few points"}

    event = analyze_watering(samples, settle_window_s=settle_window_s)
    if event.get("detected"):
        start_ts = event["peak_ts"] + settle_window_s
        tail = [(t, v) for t, v in samples if t >= start_ts]
    else:
        tail = list(samples)

    if len(tail) < min_points:
        return {"per_day": None, "per_hour": None, "n_points": len(tail), "reason": "too few points"}

    slopes = []
    for i in range(len(tail)):
        for j in range(i + 1, len(tail)):
            dt = tail[j][0] - tail[i][0]
            if dt <= 0:
                continue
            slopes.append((tail[j][1] - tail[i][1]) / dt)

    if not slopes:
        return {"per_day": None, "per_hour": None, "n_points": len(tail), "reason": "too few points"}

    slope_per_sec = statistics.median(slopes)  # negative = drying, positive = rising

    eps = 1e-9
    if slope_per_sec >= -eps:
        reason = "rising in window" if slope_per_sec > eps else "flat"
        return {"per_day": 0.0, "per_hour": 0.0, "n_points": len(tail), "reason": reason}

    drying_per_sec = -slope_per_sec
    per_day  = drying_per_sec * 86400
    per_hour = drying_per_sec * 3600
    return {
        "per_day": per_day,
        "per_hour": per_hour,
        "n_points": len(tail),
        "reason": f"drying ~{per_day:.1f}%/day",
    }


def days_until_dry(
    current_moist: float,
    drydown_rate_per_day: float | None,
    dry_threshold: float,
) -> dict[str, Any]:
    """
    Project days until `current_moist` reaches `dry_threshold`, given a
    drydown_rate() per_day figure.

    Returns {"days": float | None, "label": str}. days is None (label "not
    drying") when the rate is None/zero/negative (i.e. not currently drying —
    flat, rising, or unknown). Far projections (>=14 days) are clamped to a
    "2+ weeks" label rather than reported as an overconfident exact number.
    """
    if drydown_rate_per_day is None or drydown_rate_per_day <= 0:
        return {"days": None, "label": "not drying"}

    deficit = current_moist - dry_threshold
    if deficit <= 0:
        return {"days": 0.0, "label": "today"}

    days = deficit / drydown_rate_per_day

    if days < 1:
        label = "today"
    elif days >= 14:
        label = "2+ weeks"
    else:
        n = round(days)
        label = f"~{n} day" if n == 1 else f"~{n} days"

    return {"days": days, "label": label}


# ── Growing Degree Days + per-bed ET/water balance ───────────────────────────

def gdd_daily(tmax_f: float, tmin_f: float, base_temp_f: float) -> float:
    """
    Single-day Growing Degree Days: (Tmax+Tmin)/2 - Tbase.

    Tmax/Tmin are floor-clamped to base_temp_f BEFORE averaging — the
    standard agronomic convention (NOAA/extension-service GDD guides): a day
    whose entire range sits below base contributes exactly 0, and a day
    where only the low dips below base isn't artificially deflated by
    averaging in a below-base low. Never negative.
    """
    tmax = max(tmax_f, base_temp_f)
    tmin = max(tmin_f, base_temp_f)
    return max(0.0, (tmax + tmin) / 2.0 - base_temp_f)


def gdd_base_for_bed(
    plants: list[str],
    custom_bases: dict[str, float] | None = None,
) -> tuple[float, str] | None:
    """
    (Tbase °F, reference crop key) for a bed's recognised crops.

    Tbase is the HIGHEST base temperature among the bed's crops — the most
    conservative choice (mirrors bed_moisture_band's intersection logic): no
    GDD accrues on a day too cold for the pickiest crop in the bed. The crop
    that produced that Tbase also doubles as the bed's reference crop for
    gdd_growth_stage()/KC_MID lookups, since a mixed bed has no single true
    growth curve — using the pickiest crop keeps both numbers consistent
    with each other without a separate "primary crop" config field.

    Returns None when no recognised crop is in `plants`.
    """
    bases = dict(GDD_BASE_F)
    if custom_bases:
        bases.update(custom_bases)

    candidates = [(bases[p], p) for p in plants if p in bases]
    if not candidates:
        return None
    base_f, crop_key = max(candidates, key=lambda c: c[0])
    return base_f, crop_key


def gdd_growth_stage(cumulative_gdd: float, crop_key: str) -> dict[str, Any]:
    """
    Classify a bed's cumulative GDD into a growth stage for `crop_key`
    (resolved to its GDD_STAGES family via _gdd_family — pass either a
    variant like 'tomato_cherry' or a family key like 'tomato').

    Returns:
      {
        "stage":             "germination"|"vegetative"|"flowering"|"fruiting"|"maturity"|"unrecognized",
        "pct_to_maturity":   0-100+ (can exceed 100 once past maturity), or None if unrecognized,
        "gdd_into_stage":    GDD accrued since this stage's breakpoint, or None if unrecognized,
        "gdd_to_next_stage": GDD remaining to the next breakpoint, or None at/after maturity/unrecognized,
      }
    """
    fam = _gdd_family(crop_key)
    if fam is None:
        return {
            "stage": "unrecognized",
            "pct_to_maturity": None,
            "gdd_into_stage": None,
            "gdd_to_next_stage": None,
        }

    breakpoints = GDD_STAGES[fam]
    maturity = breakpoints["maturity"]
    pct = round((cumulative_gdd / maturity) * 100.0, 1) if maturity else None

    stage = _GDD_STAGE_ORDER[0]
    next_gdd: float | None = None
    for i, name in enumerate(_GDD_STAGE_ORDER):
        if cumulative_gdd >= breakpoints[name]:
            stage = name
            next_gdd = (
                breakpoints[_GDD_STAGE_ORDER[i + 1]]
                if i + 1 < len(_GDD_STAGE_ORDER) else None
            )
        else:
            break

    gdd_into_stage = cumulative_gdd - breakpoints[stage]
    gdd_to_next_stage = (next_gdd - cumulative_gdd) if next_gdd is not None else None

    return {
        "stage": stage,
        "pct_to_maturity": pct,
        "gdd_into_stage": round(gdd_into_stage, 1),
        "gdd_to_next_stage": round(gdd_to_next_stage, 1) if gdd_to_next_stage is not None else None,
    }


def project_harvest_date(
    cumulative_gdd: float,
    maturity_gdd: float,
    avg_gdd_per_day: float | None,
    today: date,
) -> dict[str, Any]:
    """
    Project the harvest (maturity) date from the current GDD pace.

    Mirrors days_until_dry()'s shape/philosophy: {days, date, label}. days is
    None (label "not enough data") when avg_gdd_per_day is None/zero/negative.
    Already-mature beds return {"days": 0.0, ..., "label": "ready"}. Far
    projections (>=60 days out) clamp to a "60+ days" label rather than a
    false-precise date, same spirit as days_until_dry's "2+ weeks" clamp.
    """
    remaining = maturity_gdd - cumulative_gdd
    if remaining <= 0:
        return {"days": 0.0, "date": today.isoformat(), "label": "ready"}

    if avg_gdd_per_day is None or avg_gdd_per_day <= 0:
        return {"days": None, "date": None, "label": "not enough data"}

    days = remaining / avg_gdd_per_day

    if days >= 60:
        return {"days": days, "date": None, "label": "60+ days"}

    harvest_date = today + timedelta(days=round(days))
    n = round(days)
    label = f"~{n} day" if n == 1 else f"~{n} days"
    return {"days": days, "date": harvest_date.isoformat(), "label": label}


def maturity_gdd_for_crop(crop_key: str) -> float | None:
    """
    Cumulative GDD (°F-days) at maturity/first-harvest for `crop_key`
    (resolved via _gdd_family — accepts a variant like 'tomato_cherry' or a
    family key like 'tomato'). Returns None for unrecognised crops.
    """
    fam = _gdd_family(crop_key)
    if fam is None:
        return None
    return GDD_STAGES[fam]["maturity"]


def kc_for_crop(crop_key: str, custom_kc: dict[str, float] | None = None) -> float | None:
    """
    FAO-56 mid-season crop coefficient for `crop_key` (resolved via
    _gdd_family — accepts a variant like 'tomato_cherry' or a family key
    like 'tomato'). Returns None for unrecognised crops.
    """
    kc_table = dict(KC_MID)
    if custom_kc:
        kc_table.update(custom_kc)
    fam = _gdd_family(crop_key)
    if fam is None:
        return None
    return kc_table.get(fam)


def etc_from_kc(et0_in: float, kc: float) -> float:
    """
    Crop evapotranspiration (FAO-56): ETc = ET0 x Kc.

    et0_in: reference evapotranspiration in inches (e.g. Open-Meteo's
            et0_fao_evapotranspiration — already the Penman-Monteith standard).
    kc: crop coefficient, e.g. from KC_MID.
    """
    return et0_in * kc


def estimated_irrigation_in(
    absorbed_moisture_pct: float,
    root_zone_depth_in: float,
    awc_in_per_in: float,
) -> float:
    """
    MODELED ESTIMATE of irrigation applied, not a direct measurement — there
    is no flow meter or rain gauge on the beds. Converts a soil-moisture-%
    rise (analyze_watering()'s `absorbed` field, from the WH51 sensor) into
    an inches-of-water equivalent, assuming the rise is uniform across the
    effective root zone:

      inches = (absorbed_pct / 100) * root_zone_depth_in * awc_in_per_in

    awc_in_per_in: available water capacity of the soil, inches of water per
                   inch of soil depth. Typical raised-bed potting-mix blends
                   run ~0.15-0.20 in/in.
    root_zone_depth_in: effective root zone depth in inches (shallower for
                   peas ~6in, deeper for tomato/eggplant ~10-12in).

    Clamped to >= 0 — a moisture drop is not negative irrigation.
    """
    absorbed = max(0.0, absorbed_moisture_pct)
    return (absorbed / 100.0) * root_zone_depth_in * awc_in_per_in


def bed_water_balance(rain_in: float, irrigation_in: float, etc_in: float) -> float:
    """
    Net daily per-bed water balance in inches: rain + irrigation - ETc.

    Same sign convention as et0_water_balance(): positive = surplus (bed
    received more water than it used), negative = deficit (needs watering).
    """
    return rain_in + irrigation_in - etc_in
