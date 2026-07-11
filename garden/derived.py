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
  learned_moisture_band(values)    → (min%, max%) | None — self-calibrated from a bed's own history
  effective_moisture_band(...)     → learned band, falling back to the crop band
  family_labels(plants)            → list of unique lowercase crop-family names
  analyze_watering(samples)        → {detected, baseline, peak, settled, quality, ...}
  drydown_rate(samples)            → {per_day, per_hour, n_points, reason}
  days_until_dry(moist, rate, dry_threshold) → {days, label}

CROP_RANGES — default ideal soil-moisture/temp ranges per vegetable type.

Watering-lifecycle functions (analyze_watering/drydown_rate/days_until_dry) take
samples as list[tuple[float, float]] of (epoch_seconds, moisture_pct), oldest→newest.
Always feed them a NARROW window (event ≤2h, drydown ≤48h) — storage.series()
bucket-averages wide windows down to ~350 points, which smears a multi-minute
watering spike and biases these estimates.
"""

from __future__ import annotations

import math
import statistics
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


def learned_moisture_band(
    values: list[float],
    crop_band: tuple[float, float] | None,
    *,
    min_points: int = 200,
    min_spread: float = 8.0,
    dry_pctile: float = 10.0,
    wet_pctile: float = 90.0,
    dry_frac: float = 0.30,
    wet_frac: float = 0.90,
) -> tuple[float, float] | None:
    """
    Self-calibrated (min%, max%) soil-moisture band for a bed, learned from its
    OWN recent readings rather than an absolute crop range.

    Rationale: a capacitive soil sensor reads the soil's dielectric, so loose
    fresh soil (more air pockets) reads a lower % than compacted soil at the
    same plant-available water. A fixed crop band (e.g. tomato 50-80%) can
    permanently misjudge a bed whose soil never reaches that absolute range.
    Instead, each watering cycle traces a sawtooth: the top of the post-water
    plateau approximates that bed's field capacity ("wet"), and the pre-water
    low approximates its dry point — both intrinsically compaction-adjusted.

    values:     recent moisture % readings for one bed's sensor, most-recent
                window first or in any order (order doesn't matter here).
    crop_band:  the bed's crop-derived (min, max) from bed_moisture_band(),
                unused by the math but accepted so callers can pass it through
                one call site; NOT used as a fallback here (see
                effective_moisture_band for that).
    min_points: minimum sample count required to trust the learned band —
                below this, historical coverage is too thin (e.g. a bed just
                added this season).
    min_spread: minimum (wet_pctile - dry_pctile) percentage-point spread
                required — guards against a flatlined/stuck sensor or a bed
                that's never actually dried down, where percentiles collapse
                and would produce a meaninglessly narrow band.
    dry_pctile/wet_pctile: robust (outlier-resistant) stand-ins for "driest
                observed" / "field capacity", using percentiles instead of
                min/max so a single sensor glitch doesn't skew the band.
    dry_frac/wet_frac: where within the observed [floor, fc] envelope the
                effective dry/wet cutoffs sit (30%/90% by default — "dry"
                trips a bit above the true floor so an alert has lead time;
                "wet" sits near field capacity).

    Returns None when there isn't enough history or spread to trust the
    result — callers should fall back to the crop-derived band in that case
    (see effective_moisture_band).
    """
    if len(values) < min_points:
        return None

    floor = _percentile(values, dry_pctile)
    fc    = _percentile(values, wet_pctile)
    spread = fc - floor
    if spread < min_spread:
        return None

    moist_min = floor + dry_frac * spread
    moist_max = floor + wet_frac * spread
    return (moist_min, moist_max)


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (0-100), no numpy dependency."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return ordered[0]
    rank = (pct / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def effective_moisture_band(
    values: list[float],
    plants: list[str],
    custom_ranges: dict[str, Any] | None = None,
    learning_kwargs: dict[str, Any] | None = None,
) -> tuple[float, float] | None:
    """
    The band bed_stress should actually use for a bed: self-learned from its
    own history when there's enough of it, otherwise the crop-derived band.

    values:          recent moisture % readings for the bed's sensor.
    plants/custom_ranges: forwarded to bed_moisture_band() for the fallback.
    learning_kwargs: forwarded to learned_moisture_band() (min_points,
                     min_spread, dry_pctile, wet_pctile, dry_frac, wet_frac).
    """
    crop_band = bed_moisture_band(plants, custom_ranges)
    learned = learned_moisture_band(values, crop_band, **(learning_kwargs or {}))
    return learned or crop_band


def bed_stress(
    plants: list[str],
    soil_moist: float,
    air_temp_f: float,
    custom_ranges: dict[str, Any] | None = None,
    band_override: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """
    Assess the stress state of a raised bed.

    plants:        crop keys (e.g. ['tomato', 'tomato', 'tomato']).
    soil_moist:    current soil moisture % from the bed's WH51 sensor.
    air_temp_f:    outdoor air temperature °F.
    custom_ranges: optional per-crop overrides from config.yaml crops: block
                   (each value may contain 'moist' and/or 'temp' lists).
    band_override: optional (min%, max%) to use for the moisture check instead
                    of the crop-derived band — pass a bed's self-learned band
                    (see learned_moisture_band/effective_moisture_band) so the
                    dry/wet call reflects that bed's own soil, not a one-size
                    crop range. Temperature-stress logic is unaffected.

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

    # Aggregate range: intersection (strictest min and max across crops),
    # unless the caller supplied a self-learned band for this specific bed.
    moist_min, moist_max = band_override or bed_moisture_band(plants, custom_ranges)
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
