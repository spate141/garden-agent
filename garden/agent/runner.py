"""
runner.py — Evaluate rules, apply cooldowns, dispatch Telegram alerts.

Two entry points:
  evaluate_instant(snap_id, ts, metrics) — called inline on every POST
  run_cron_tick()                        — called by the systemd timer every 15 min

The cron tick also handles the daily morning brief (replaces the old
heartbeat) and the once-daily GDD/water-balance accumulation.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from garden import derived, storage
from garden.agent import llm
from garden.agent.rules import RuleResult, run_cron, run_instant
from garden.agent.weather import forecast_summary, get_forecast
from garden.config import cfg
from garden.telegram import tg

log = logging.getLogger("garden.runner")

# Maps rule_id prefix → cooldown key in config.yaml cooldowns dict
_COOLDOWN_KEY: dict[str, str] = {
    "soil_moisture_low":          "soil_moisture_low_minutes",
    "soil_moisture_rapid_drop":   "soil_moisture_rapid_drop_minutes",
    "soil_moisture_rapid_rise":   "soil_moisture_rapid_rise_minutes",
    "battery_low":                "battery_low_minutes",
    "temp_frost":                 "temp_frost_minutes",
    "temp_heat":                  "temp_heat_minutes",
    "watchdog":                   "watchdog_minutes",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cooldown_minutes(rule_id: str) -> int:
    prefix = rule_id.split(":")[0]
    key = _COOLDOWN_KEY.get(prefix, "watchdog_minutes")
    return cfg.cooldowns.get(key, 60)


def _in_cooldown(state: dict, rule_id: str) -> bool:
    """True if this rule fired within its cooldown window (time-based, regardless of active state)."""
    last_fired = state.get("last_fired_ts", "")
    if not last_fired:
        return False
    cooldown = _cooldown_minutes(rule_id)
    try:
        last_dt = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        return elapsed < cooldown
    except ValueError:
        return False


# rapid_drop is inhibited for N minutes after a watering event (rapid_rise) on the same bed.
# This prevents sensor-spike settling from being misread as a drainage/leak event.
_INHIBIT_AFTER: dict[str, str] = {
    "soil_moisture_rapid_drop": "soil_moisture_rapid_rise",
}


def _inhibited_by_watering(result: RuleResult) -> bool:
    prefix = result.rule_id.split(":")[0]
    inhibitor_prefix = _INHIBIT_AFTER.get(prefix)
    if not inhibitor_prefix:
        return False
    inhibitor_id = f"{inhibitor_prefix}:{result.sensor_key}"
    inh_state = storage.get_alert_state(inhibitor_id)
    last_fired = inh_state.get("last_fired_ts", "")
    if not last_fired:
        return False
    inhibit_minutes = cfg.cooldowns.get("rapid_drop_after_watering_minutes", 120)
    try:
        last_dt = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        return elapsed < inhibit_minutes
    except ValueError:
        return False


def _dispatch(result: RuleResult) -> None:
    """Fire the alert and update alert_state."""
    now = _now_iso()
    # Bed-specific alerts get the bed's crops appended to the title (e.g. "· zucchini, eggplant")
    # so the gardener doesn't have to remember what's planted where. Non-bed alerts (temp,
    # gateway watchdog) get "" back and the title is left alone.
    crops = cfg.bed_crops_label(result.sensor_key)
    title = f"{result.title} · {crops}" if crops else result.title
    log.info("Alert firing: %s — %s", result.rule_id, title)
    if result.rule_id.startswith("watchdog:"):
        body = result.body
    else:
        body = llm.write_alert(result.rule_id, result.sensor_key, title, result.body)
    tg(title, body)
    storage.set_alert_state(result.rule_id, result.sensor_key, active=True, last_fired_ts=now)


def _evaluate(results: list[RuleResult]) -> None:
    for result in results:
        state = storage.get_alert_state(result.rule_id)
        was_active = bool(state.get("active", 0))

        if result.fired:
            if _in_cooldown(state, result.rule_id):
                log.debug("Suppressed (cooldown): %s", result.rule_id)
                continue
            if _inhibited_by_watering(result):
                log.debug("Suppressed (post-watering inhibit): %s", result.rule_id)
                continue
            _dispatch(result)
        else:
            if was_active:
                log.info("Condition cleared: %s", result.rule_id)
                storage.set_alert_state(result.rule_id, result.sensor_key, active=False)


def evaluate_instant(snap_id: int, ts: str, metrics: dict) -> None:
    """Run instant rules after a POST. Called from main.py."""
    try:
        results = run_instant()
        _evaluate(results)
    except Exception:
        log.exception("evaluate_instant failed for snapshot %d", snap_id)


def run_cron_tick() -> None:
    """Run cron rules + daily brief. Called by the systemd timer every 15 min."""
    log.info("Cron tick starting")
    try:
        results = run_cron()
        _evaluate(results)
    except Exception:
        log.exception("Cron rule evaluation failed")

    try:
        _maybe_daily_brief()
    except Exception:
        log.exception("Daily brief failed")

    try:
        _maybe_daily_agronomy_accumulation()
    except Exception:
        log.exception("Daily agronomy accumulation failed")

    log.info("Cron tick complete")


# ── Daily morning brief ───────────────────────────────────────────────────────

_BRIEF_RULE_ID = "daily_brief"


def _bed_drydown_line(bed: dict, moist_key: str | None, soil_moist: float) -> str | None:
    """
    'BedName: drying ~X%/day, dry in ~N days' for the daily brief, or None when
    there isn't enough history / plant data to project. Suppressed while the
    bed is still settling from a recent watering (forecast unreliable then).
    """
    if not moist_key:
        return None
    band = derived.bed_moisture_band(bed.get("plants", []), cfg.crops)
    if band is None:
        return None
    dry_threshold = band[0]

    lifecycle_cfg = cfg.thresholds.get("watering_lifecycle", {})
    lookback_hours = lifecycle_cfg.get("drydown_lookback_hours", 48)
    settle_window_s = lifecycle_cfg.get("settle_window_minutes", 60) * 60

    rows = storage.series(moist_key, hours=lookback_hours)
    samples = [
        (datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp(), r["value"])
        for r in rows
    ]
    event = derived.analyze_watering(samples, settle_window_s=settle_window_s)
    if event.get("detected") and event.get("settling"):
        return None  # still settling from a recent watering -- forecast not meaningful yet

    rate = derived.drydown_rate(samples, settle_window_s=settle_window_s)
    dtd = derived.days_until_dry(soil_moist, rate["per_day"], dry_threshold)
    if dtd["days"] is None:
        return None

    name = bed.get("name", bed.get("id"))
    return f"  {name}: drying ~{rate['per_day']:.1f}%/day, dry in {dtd['label']}"


def _sensor_summary() -> str:
    """
    Build a compact sensor summary for the LLM from the latest readings.
    Includes raw readings, derived agronomic metrics, and per-bed stress assessment.
    """
    rows = storage.latest()
    if not rows:
        return "No sensor data available yet."

    PRIORITY = ["soilmoisture1", "soilmoisture2", "soilmoisture3", "soilmoisture4",
                "temp_f", "humidity", "soilbatt1", "soilbatt2", "soilbatt3", "soilbatt4"]
    DERIVED   = ["vpd_kpa", "dewpoint_f", "heatindex_f"]
    by_key    = {r["sensor_key"]: r for r in rows}

    lines: list[str] = []

    # Priority raw readings
    for key in PRIORITY:
        if key in by_key:
            r = by_key[key]
            lines.append(f"  {cfg.sensor_label(key)}: {r['value']:.1f}{r['unit']}")

    # Derived agronomic metrics (high interpretive value for the LLM)
    derived_lines: list[str] = []
    for key in DERIVED:
        if key in by_key:
            r = by_key[key]
            derived_lines.append(f"  {cfg.sensor_label(key)}: {r['value']:.2f}{r['unit']}")
    if derived_lines:
        lines.append("  --- derived ---")
        lines.extend(derived_lines)

    # Per-bed stress assessment
    try:
        src_temp_key = cfg.derived.get("source", {}).get("temp", "temp1_f")
        air_temp_row = by_key.get(src_temp_key)
        air_temp_f   = air_temp_row["value"] if air_temp_row else None
        if air_temp_f is not None:
            lines.append("  --- bed stress ---")
            for bed in cfg.dashboard.get("beds", []):
                moist_key = bed.get("sensors", {}).get("soil_moisture")
                moist_row = by_key.get(moist_key) if moist_key else None
                soil_moist = moist_row["value"] if moist_row else None
                if soil_moist is not None:
                    stress = derived.bed_stress(
                        bed.get("plants", []), soil_moist, air_temp_f, cfg.crops
                    )
                    lines.append(f"  {bed.get('name', bed.get('id'))}: {stress['reason']}")
    except Exception:
        log.debug("Bed stress assessment skipped", exc_info=True)

    # Per-bed drydown forecast — when is each bed next due for watering.
    try:
        lines_drydown: list[str] = []
        for bed in cfg.dashboard.get("beds", []):
            moist_key = bed.get("sensors", {}).get("soil_moisture")
            moist_row = by_key.get(moist_key) if moist_key else None
            soil_moist = moist_row["value"] if moist_row else None
            if soil_moist is None:
                continue
            line = _bed_drydown_line(bed, moist_key, soil_moist)
            if line:
                lines_drydown.append(line)
        if lines_drydown:
            lines.append("  --- drydown forecast ---")
            lines.extend(lines_drydown)
    except Exception:
        log.debug("Drydown forecast skipped", exc_info=True)

    # Remaining sensors (not in priority or derived lists)
    skip = set(PRIORITY) | set(DERIVED)
    for key, r in by_key.items():
        if key not in skip:
            lines.append(f"  {cfg.sensor_label(key)}: {r['value']:.1f}{r['unit']}")

    return "\n".join(lines) if lines else "No sensor data available."


def _local_now() -> datetime:
    """Current time in the configured local timezone."""
    tz_name = cfg.location.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.warning("Unknown timezone %r, falling back to UTC", tz_name)
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


def _rule_already_fired_today(rule_id: str, local_now: datetime) -> bool:
    """
    True if alert_state[rule_id].last_fired_ts falls on local_now's local
    date. Shared once-per-local-day dedup check -- used by both the daily
    brief and the agronomy accumulation job, each with their own rule_id.
    """
    state = storage.get_alert_state(rule_id)
    last_fired = state.get("last_fired_ts", "")
    if not last_fired:
        return False
    try:
        last_dt = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
        tz_name = cfg.location.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            log.warning("Unknown timezone %r in _rule_already_fired_today, falling back to UTC", tz_name)
            tz = ZoneInfo("UTC")
        return last_dt.astimezone(tz).date() == local_now.astimezone(tz).date()
    except Exception:
        return False


def _already_sent_today(local_now: datetime) -> bool:
    """True if the brief was already sent today (local date)."""
    return _rule_already_fired_today(_BRIEF_RULE_ID, local_now)


def send_daily_brief(force: bool = False) -> None:
    """
    Send the morning garden brief. Called by run_cron_tick() and the --brief CLI flag.

    Args:
        force: if True, skip the hour check and dedup (for testing / manual send).
    """
    if not cfg.daily_brief.get("enabled", True):
        log.info("Daily brief disabled in config")
        return

    local_now = _local_now()

    if not force:
        hour_local = cfg.daily_brief.get("hour_local", 7)
        if local_now.hour != hour_local:
            return
        if _already_sent_today(local_now):
            log.debug("Daily brief already sent today, skipping")
            return

    log.info("Sending daily brief (force=%s, local time=%s)", force, local_now.strftime("%H:%M %Z"))

    fc = get_forecast()
    sensor_sum = _sensor_summary()
    body = llm.write_daily_brief(fc, sensor_sum)

    weather_line = forecast_summary(fc)
    title = f"Morning Brief · {local_now.strftime('%a %b %-d')}"

    tg(title, body, html=True)
    storage.set_alert_state(_BRIEF_RULE_ID, "", active=False, last_fired_ts=_now_iso())
    log.info("Daily brief sent")


def _maybe_daily_brief() -> None:
    send_daily_brief(force=False)


# ── Daily agronomy accumulation (GDD + per-bed ET/water balance) ─────────────
#
# Once per local day, persist each bed's GDD and water-balance figures to
# bed_daily_agronomy (see garden/storage.py). Same once-per-day idempotency
# pattern as send_daily_brief/_already_sent_today above, keyed by its own
# alert_state rule_id per bed so it can't collide with the brief's dedup.

_AGRONOMY_RULE_PREFIX = "agronomy_accum"
_MAX_BACKFILL_DAYS = 366  # defensive cap in case planted_on is garbage/far in the past


def _agronomy_already_run_today(bed_id: str, local_now: datetime) -> bool:
    return _rule_already_fired_today(f"{_AGRONOMY_RULE_PREFIX}_{bed_id}", local_now)


def _local_day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[str, str]:
    """UTC ISO bounds [start, end) for one local calendar day."""
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    end_utc = end_local.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return start_utc, end_utc


def _backfill_gdd(bed_id: str, base_f: float, temp_key: str, tz: ZoneInfo, today: date) -> None:
    """
    On a bed's first accumulation run, backfill gdd_daily/gdd_cumulative for
    every day from planted_on up to (not including) today, using whatever
    local sensor history already exists for temp_key. Without this, GDD
    would silently start counting from whichever day the cron first ran
    instead of the actual planting date.

    Water balance is intentionally NOT backfilled -- weather.py only caches
    TODAY's Open-Meteo forecast in-process, nothing historical is persisted,
    so there's no accurate past rain/ET0 to backfill from. It simply starts
    accruing from today forward; a documented gap, not a bug.

    Days with no sensor history (e.g. before the station was recording) are
    skipped silently -- they contribute 0 GDD rather than crashing.
    """
    planted_str = cfg.bed_planted_on(bed_id)
    try:
        day = date.fromisoformat(planted_str) if planted_str else today
    except ValueError:
        log.warning("Bed %s has an unparseable planted_on %r, skipping GDD backfill", bed_id, planted_str)
        return

    n = 0
    while day < today and n < _MAX_BACKFILL_DAYS:
        day_str = day.isoformat()
        start_utc, end_utc = _local_day_bounds_utc(day, tz)
        day_temp = storage.day_stats(temp_key, start_utc, end_utc)
        if day_temp is not None:
            day_gdd = derived.gdd_daily(day_temp["max"], day_temp["min"], base_f)
            gdd_cum = storage.bed_gdd_cumulative_before(bed_id, day_str) + day_gdd
            storage.upsert_bed_agronomy(
                bed_id, day_str,
                tmax_f=day_temp["max"], tmin_f=day_temp["min"],
                gdd_daily=day_gdd, gdd_cumulative=gdd_cum,
            )
        day += timedelta(days=1)
        n += 1

    if n:
        log.info("Backfilled GDD for %s: %d day(s) from %s", bed_id, n, planted_str or today.isoformat())


def run_daily_agronomy_accumulation(force: bool = False) -> None:
    """
    Once per local day (config: agronomy.accumulation_hour_local, default 23
    — late enough that the day's temp range and forecast snapshot are close
    to final), compute and persist each bed's GDD + water-balance row.

    Called by run_cron_tick(); mirrors send_daily_brief's force/hour/dedup shape.
    """
    if not cfg.agronomy.get("enabled", True):
        return

    local_now = _local_now()
    if not force:
        hour_local = cfg.agronomy.get("accumulation_hour_local", 23)
        if local_now.hour != hour_local:
            return

    today = local_now.date()
    today_str = today.isoformat()
    fc = get_forecast()
    temp_key = cfg.agronomy.get("gdd_temp_key", "temp_f")
    gdd_base_overrides = cfg.agronomy.get("gdd_base_overrides") or {}
    kc_overrides = cfg.agronomy.get("kc_overrides") or {}
    beds_cfg = cfg.agronomy.get("beds", {})

    tz_name = cfg.location.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    # gdd_temp_key is one global config value, not per-bed -- fetch once
    # rather than re-querying the same stats identically for every bed.
    temp_stats = storage.stats(temp_key, hours=24)
    if temp_stats is None:
        log.debug("Agronomy accumulation: no %s data yet, skipping all beds", temp_key)
        return

    for bed in cfg.dashboard.get("beds", []):
        bed_id = bed.get("id")
        if not bed_id:
            continue
        if not force and _agronomy_already_run_today(bed_id, local_now):
            continue

        base = derived.gdd_base_for_bed(bed.get("plants", []), gdd_base_overrides)
        if base is None:
            continue  # no recognised crops in this bed
        base_f, ref_crop = base

        if storage.get_bed_agronomy_latest(bed_id) is None:
            _backfill_gdd(bed_id, base_f, temp_key, tz, today)

        gdd_today = derived.gdd_daily(temp_stats["max"], temp_stats["min"], base_f)

        # Irrigation estimate from the day's soil-moisture rise. A 24h window
        # here (not the <=2h analyze_watering()'s docstring recommends for
        # precise spike CHARACTERIZATION) is deliberate: this only needs
        # "did watering happen at all today," so a bed watered in the
        # morning isn't invisible to this nightly job. Bucket smearing at
        # 24h is mild (~4min buckets vs the ~60s ingest interval) compared
        # to the multi-hour smearing a week-long window would cause.
        moist_key = bed.get("sensors", {}).get("soil_moisture")
        watering: dict = {}
        if moist_key:
            rows = storage.series(moist_key, hours=24)
            samples = [
                (datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp(), r["value"])
                for r in rows
            ]
            watering = derived.analyze_watering(samples)

        bed_agro_cfg = beds_cfg.get(bed_id, {})
        root_zone_in = bed_agro_cfg.get("root_zone_depth_in", 9.0)
        awc = bed_agro_cfg.get("awc_in_per_in", 0.17)
        irrigation_in = (
            derived.estimated_irrigation_in(watering["absorbed"], root_zone_in, awc)
            if watering.get("detected") else 0.0
        )

        kc = derived.kc_for_crop(ref_crop, kc_overrides) or 1.0
        et0_in = fc.get("et0_in") if fc else None
        rain_in = (fc.get("precip_in") if fc else None) or 0.0
        etc_in = derived.etc_from_kc(et0_in, kc) if et0_in is not None else 0.0
        wb_daily = derived.bed_water_balance(rain_in, irrigation_in, etc_in)

        is_good_soak = watering.get("quality") == "good_soak"

        # Cumulative totals are recomputed from row history via SQL SUM each
        # time (storage.bed_gdd_cumulative_before /
        # bed_water_balance_cumulative_since_reset), not chained off a
        # stored running total -- so reprocessing today (e.g. a forced
        # --agronomy rerun) recomputes the same value instead of double-
        # counting today's contribution on top of itself. GDD always
        # accumulates from planted_on and never resets on a watering event
        # (it's a phenology clock); water balance resets to just today's
        # value on a good soak (re-anchors "deficit since last real
        # recharge"), same spirit as drydown_rate's post-watering re-anchor.
        gdd_cum = storage.bed_gdd_cumulative_before(bed_id, today_str) + gdd_today
        wb_cum = (
            wb_daily if is_good_soak
            else storage.bed_water_balance_cumulative_since_reset(bed_id, today_str) + wb_daily
        )

        storage.upsert_bed_agronomy(
            bed_id, today_str,
            tmax_f=temp_stats["max"], tmin_f=temp_stats["min"],
            gdd_daily=gdd_today, gdd_cumulative=gdd_cum,
            et0_in=et0_in, etc_in=etc_in,
            rain_in=rain_in, irrigation_est_in=irrigation_in,
            water_balance_daily=wb_daily, water_balance_cumulative=wb_cum,
            reset_reason="good_soak" if is_good_soak else "",
        )
        storage.set_alert_state(f"{_AGRONOMY_RULE_PREFIX}_{bed_id}", "", active=False, last_fired_ts=_now_iso())
        log.info(
            "Agronomy accumulation %s: +%.1f GDD (%.1f total), water balance %+.2fin (%+.2fin total)",
            bed_id, gdd_today, gdd_cum, wb_daily, wb_cum,
        )


def _maybe_daily_agronomy_accumulation() -> None:
    run_daily_agronomy_accumulation(force=False)


# ── CLI entry point (used by garden-cron.service) ────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="garden-agent cron runner")
    parser.add_argument("--cron",  action="store_true", help="Run cron tick (rules + brief + agronomy)")
    parser.add_argument("--brief", action="store_true", help="Force-send morning brief now (ignores hour/dedup)")
    parser.add_argument("--agronomy", action="store_true", help="Force-run GDD/water-balance accumulation now (ignores hour/dedup)")
    args = parser.parse_args()

    if args.brief:
        storage.init_db()
        send_daily_brief(force=True)
    elif args.agronomy:
        storage.init_db()
        run_daily_agronomy_accumulation(force=True)
    elif args.cron:
        run_cron_tick()
