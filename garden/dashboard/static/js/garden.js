/* ════════════════════════════════════════════════════════════════════════════
   CONFIG
   ════════════════════════════════════════════════════════════════════════════ */

const CHARTS = window.GARDEN_CONFIG.CHARTS;
/* Trends time-range window (§6), shared by the background chart-refresh cycle
   so every chart, sparkline, and trend arrow on the page speaks the same window.
   Mutated by setTrendsHours(); defaults to 24h. */
let HOURS    = 24;
const TZ     = window.GARDEN_CONFIG.TZ;

/** Format a UTC ISO timestamp to local time: "HH:MM MM-DD-YYYY" */
function _fmtLocalTs(isoUtc) {
  if (!isoUtc) return 'no data yet';
  const d   = new Date(isoUtc);  /* already has +00:00 offset from DB */
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, hourCycle: 'h23',
    hour: '2-digit', minute: '2-digit',
    month: '2-digit', day: '2-digit', year: 'numeric',
  });
  const p = {};
  fmt.formatToParts(d).forEach(function (part) {
    if (part.type !== 'literal') p[part.type] = part.value;
  });
  return p.hour + ':' + p.minute + ' ' + p.month + '-' + p.day + '-' + p.year;
}

/** Bed/plant configuration — edit dashboard.beds in config.yaml.
 *  Plant types: tomato | eggplant | okra | peas | sweet_pepper | hot_pepper | zucchini */
const BEDS = window.GARDEN_CONFIG.BEDS;

const WEATHER_KEYS = window.GARDEN_CONFIG.WEATHER_KEYS;

/** Thresholds from config.yaml -- same values that fire Telegram alerts.
 *  Injected server-side so the visual wilt/asleep triggers agree with the agent rules. */
const G = window.GARDEN_CONFIG.G;

/** Sun/moon times from Open-Meteo (UTC epoch seconds).
 *  null when location is unavailable or weather is disabled — sky falls back to
 *  the tod() clock-hour estimate in that case.
 *  sunrise_ts / sunset_ts : today's times
 *  sunrise_ts_tomorrow    : tomorrow's sunrise (bounds the post-sunset moon arc) */
let SKY = window.GARDEN_CONFIG.SKY;

/** Live "right now" conditions from Open-Meteo's current block (short-TTL,
 *  separate from the daily forecast). null until the first /api/insights
 *  response arrives, or if weather/location is unavailable.
 *  { conditions, weather_code, rain_in, cloud_cover_pct, is_raining, intensity } */
let CURRENT = null;

/** Today's peak wind (mph), from /api/insights.forecast.wind_max_mph.
 *  Drives the full-page sky backdrop's rain-streak slant + cloud speed
 *  (sky-backdrop.css / _addSkyRainDrops). null until first insights load,
 *  or if weather is unavailable -- visuals just fall back to no wind. */
let WIND_MAX_MPH = null;

/** Latest reading per sensor_key, refreshed each poll — feeds the climate strip. */
let LATEST_READINGS = {};
/** Latest reading timestamp per sensor_key, same cadence — feeds chip staleness. */
let LATEST_TS = {};
/** Timestamp of the single freshest reading across all sensors -- drives the
 *  climate strip's "data N min old" note (redesign.md §9). */
let LATEST_UPDATED_TS = null;

/** Chart band/zone data, derived server-side from config.yaml (single source of truth).
 *  moistureBands: {[sensorKey]: {min, max, label, crops}} per-bed optimal range.
 *  vpdBands:      [{upTo, label}] VPD zone breakpoints.
 *  battery:       {nominal, warn, critical} voltage thresholds. */
const BANDS = window.GARDEN_CONFIG.BANDS;

/** Consolidated moisture chart group -- one entry per bed: {key, bed, plants}.
 *  Built server-side from dashboard.beds (main.py). */
const MOISTURE_GROUP = window.GARDEN_CONFIG.MOISTURE_GROUP;

/* ════════════════════════════════════════════════════════════════════════════
   VALID SPRITE SET
   ════════════════════════════════════════════════════════════════════════════ */

const SPRITES = new Set([
  'tomato', 'eggplant', 'okra', 'peas', 'sweet_pepper', 'hot_pepper', 'zucchini',
  'tomato_cherry', 'tomato_roma', 'tomato_beefsteak', 'tomato_heirloom',
  'tomato_grape', 'tomato_san_marzano',
  'sweet_pepper_red', 'sweet_pepper_green', 'sweet_pepper_yellow', 'sweet_pepper_orange',
]);
function spriteHref(type) {
  return '#sprite-' + (SPRITES.has(type) ? type : 'unknown');
}

/** Per-plant height/width multipliers -- give each type a distinct silhouette rhythm.
 *  h: height relative to base ph (tall okra vs squat tomato)
 *  w: width relative to base pw (wide tomato vs slim eggplant) */
const PLANT_META = {
  tomato:       { h: 0.82, w: 1.05 },   /* short bushy dome */
  eggplant:     { h: 1.22, w: 0.86 },   /* tall narrow teardrop */
  okra:         { h: 1.35, w: 0.78 },   /* tallest, very slim */
  peas:         { h: 1.18, w: 0.88 },   /* tall wispy vine */
  sweet_pepper: { h: 0.88, w: 1.08 },   /* medium, blocky-wide */
  hot_pepper:   { h: 1.12, w: 0.82 },   /* medium-tall, slim pod bouquet */
  zucchini:     { h: 1.05, w: 0.90 },   /* tall bowed cylinder */
  tomato_cherry:      { h: 0.78, w: 0.95 },
  tomato_roma:        { h: 0.95, w: 0.85 },
  tomato_beefsteak:   { h: 0.90, w: 1.15 },
  tomato_heirloom:    { h: 0.88, w: 1.05 },
  tomato_grape:       { h: 0.80, w: 0.90 },
  tomato_san_marzano: { h: 1.00, w: 0.80 },
  sweet_pepper_red:    { h: 0.88, w: 1.08 },
  sweet_pepper_green:  { h: 0.88, w: 1.08 },
  sweet_pepper_yellow: { h: 0.88, w: 1.08 },
  sweet_pepper_orange: { h: 0.88, w: 1.08 },
  unknown:      { h: 1.00, w: 1.00 },
};

/** Vegetable emoji per plant type -- used to label lines in the consolidated
 *  moisture/battery charts (one line per bed). */
const PLANT_EMOJI = {
  tomato:       '🍅',
  eggplant:     '🍆',
  okra:         '🌿',
  peas:         '🫛',
  sweet_pepper: '🫑',
  hot_pepper:   '🌶️',
  zucchini:     '🥒',
  tomato_cherry:      '🍅',
  tomato_roma:        '🍅',
  tomato_beefsteak:   '🍅',
  tomato_heirloom:    '🍅',
  tomato_grape:       '🍅',
  tomato_san_marzano: '🍅',
  sweet_pepper_red:    '🫑',
  sweet_pepper_green:  '🫑',
  sweet_pepper_yellow: '🫑',
  sweet_pepper_orange: '🫑',
  unknown:      '🌱',
};

/** Pick the dominant plant in a bed's plant list (tie -> first listed) and
 *  return its emoji. Used as the legend label for a bed's line. */
function bedEmoji(plants) {
  if (!plants || !plants.length) return PLANT_EMOJI.unknown;
  var counts = {};
  plants.forEach(function (p) { counts[p] = (counts[p] || 0) + 1; });
  var best = plants[0], bestCount = 0;
  plants.forEach(function (p) {
    if (counts[p] > bestCount) { best = p; bestCount = counts[p]; }
  });
  return PLANT_EMOJI[best] || PLANT_EMOJI.unknown;
}

/* ════════════════════════════════════════════════════════════════════════════
   SEEDED RNG — deterministic jitter (stable across refreshes per bed)
   ════════════════════════════════════════════════════════════════════════════ */

function strHash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return h >>> 0;
}

function mkRng(seed) {
  let s = (seed | 0) + 0x6d2b79f5;
  return function () {
    s = Math.imul(s ^ (s >>> 15), 1 | s);
    s ^= s + Math.imul(s ^ (s >>> 7), 61 | s);
    return ((s ^ (s >>> 14)) >>> 0) / 4294967296;
  };
}

/* ════════════════════════════════════════════════════════════════════════════
   COLOR UTILS
   ════════════════════════════════════════════════════════════════════════════ */

function lerpRgb(a, b, t) {
  t = Math.max(0, Math.min(1, t));
  return a.map((v, i) => Math.round(v + (b[i] - v) * t));
}

/** Compute gradient stops for the soil based on moisture pct.
 *  dry (0 %) -> soil-dry top / soil-mid bot
 *  wet (100%) -> soil-mid top / soil-wet bot
 *  Returns [topColor, botColor] as rgb() strings. */
function moistToSoilGradient(pct) {
  const t = Math.min(1, (pct || 0) / 80);
  const top = lerpRgb([156,115,80], [107,74,46], t);  /* soil-dry -> soil-mid */
  const bot = lerpRgb([107,74,46],  [ 63,42,24], t);  /* soil-mid -> soil-wet */
  return [
    'rgb(' + top.join(',') + ')',
    'rgb(' + bot.join(',') + ')',
  ];
}

/* ════════════════════════════════════════════════════════════════════════════
   TIME OF DAY
   ════════════════════════════════════════════════════════════════════════════ */

/** Clock-hour fallback bucket, used only when real sunrise/sunset (SKY) isn't
 *  available yet. Matches the 4 phases in redesign.md §3.1 -- no 'evening'
 *  bucket; the real sun-relative math below is what actually drives the sky
 *  once SKY is populated (first insights poll self-heals this on every load). */
function tod() {
  const h = new Date().getHours() + new Date().getMinutes() / 60;
  if (h < 5 || h >= 21) return 'night';
  if (h < 7)            return 'dawn';
  if (h < 18)           return 'day';
  return 'dusk';
}

/* ════════════════════════════════════════════════════════════════════════════
   CELESTIAL POSITION — arc math for sun & moon
   ════════════════════════════════════════════════════════════════════════════ */

/**
 * Returns the position of a celestial body along a parabolic arc.
 *
 * peakTop/lowTop are fixed pixel offsets from the top of the viewport, not
 * derived from the sky element's height: the full-page backdrop's .sky-band
 * is tall (60vh) so the *visual* horizon (grass/treeline in the hero, which
 * sits a fixed ~150px down: header 52px + hero margin 16px + sky-strip
 * ~86px) is only a small sliver near the top of that band. Rise/set should
 * land there, and the peak only a bit higher, in the open-sky strip above
 * the hero -- not stretched across the whole band down to its bottom, which
 * would sink the sun into the page content mid-arc.
 *
 * @param {number} nowSec   – current time, UTC epoch seconds
 * @param {number} riseSec  – rise time, UTC epoch seconds
 * @param {number} setSec   – set time, UTC epoch seconds
 * @param {number} peakTop  – px from viewport top at the highest point (noon/lunar transit)
 * @param {number} lowTop   – px from viewport top at rise/set (near the horizon)
 * @returns {object} left_pct, top_px, up
 */
function celestialPos(nowSec, riseSec, setSec, peakTop, lowTop) {
  var up   = (nowSec >= riseSec && nowSec < setSec);
  var frac = up ? Math.max(0, Math.min(1, (nowSec - riseSec) / (setSec - riseSec))) : 0;

  /* horizontal: 4 % inset on each side so the body doesn't clip the edge */
  var left_pct = 4 + frac * 92;

  /* vertical parabola: body sits at lowTop (horizon) at rise/set, peaks at
     peakTop around solar/lunar noon */
  var top_px = lowTop - 4 * frac * (1 - frac) * (lowTop - peakTop);

  return { left_pct: left_pct, top_px: top_px, up: up };
}

/**
 * Compute current moon phase from date arithmetic (no API, no library).
 * Reference new moon: 2000-01-06 18:14 UT.
 *
 * @param {Date} date
 * @returns {object} frac (0=new, 0.25=Q1, 0.5=full, 0.75=Q3), illum (0..1), waxing (bool)
 */
function moonPhase(date) {
  var REF_MS   = Date.UTC(2000, 0, 6, 18, 14);   /* reference new moon */
  var SYNODIC  = 29.5305882;                      /* mean synodic month, days */
  var elapsed  = (date.getTime() - REF_MS) / 86400000;
  var cycleFrac = ((elapsed % SYNODIC) + SYNODIC) % SYNODIC / SYNODIC;
  var illum     = (1 - Math.cos(2 * Math.PI * cycleFrac)) / 2;
  return { frac: cycleFrac, illum: illum, waxing: cycleFrac < 0.5 };
}

/**
 * Render the correct phase shape into moonEl's innerHTML using an inline SVG.
 *
 * The algorithm:
 *   - Always draw a dark circle (the unlit side).
 *   - Overlay a lit path bounded by one semicircle + the terminator ellipse.
 *   - Terminator rx = R·|cos(frac·2π)| (ranges from R at new/full to 0 at quarters).
 *   - Sweep direction flips to choose crescent vs gibbous and waxing vs waning.
 *
 * @param {HTMLElement} moonEl
 * @param {object} phase  – moonPhase() result: frac, waxing
 */
function _setMoonPhase(moonEl, phase) {
  var r = 14, cx = 15, cy = 15;
  var f = phase.frac;

  /* special cases */
  var isNew  = (f < 0.02 || f > 0.98);
  var isFull = (f > 0.48 && f < 0.52);

  /* terminator ellipse x-radius (0 at quarters, r at new/full) */
  var ex    = r * Math.cos(f * 2 * Math.PI);   /* +r → -r → +r as f goes 0→0.5→1 */
  var exAbs = Math.abs(ex).toFixed(1);
  var top   = (cy - r), bot = (cy + r);

  var litPath;
  if (!isNew && !isFull) {
    var sw;
    if (phase.waxing) {
      /* right side lit: right semicircle (CW), then terminator back to top */
      sw = (ex > 0) ? '0' : '1';    /* CCW = crescent; CW = gibbous */
      litPath = 'M ' + cx + ' ' + top +
                ' A ' + r + ' ' + r + ' 0 0 1 ' + cx + ' ' + bot +
                ' A ' + exAbs + ' ' + r + ' 0 0 ' + sw + ' ' + cx + ' ' + top;
    } else {
      /* left side lit: left semicircle (CCW), then terminator back to top */
      sw = (ex < 0) ? '1' : '0';    /* CW = gibbous; CCW = crescent */
      litPath = 'M ' + cx + ' ' + top +
                ' A ' + r + ' ' + r + ' 0 0 0 ' + cx + ' ' + bot +
                ' A ' + exAbs + ' ' + r + ' 0 0 ' + sw + ' ' + cx + ' ' + top;
    }
  }

  /* subtle craters */
  var craters = (phase.illum > 0.25)
    ? '<circle cx="11" cy="11" r="1.5" fill="rgba(180,175,150,0.3)"/>' +
      '<circle cx="19" cy="18" r="1"   fill="rgba(180,175,150,0.25)"/>'
    : '';

  var svg = '<svg width="30" height="30" viewBox="0 0 30 30"' +
            ' xmlns="http://www.w3.org/2000/svg">';
  if (isNew) {
    /* new moon: dark disc with faint halo so it's vaguely visible at night */
    svg += '<circle cx="15" cy="15" r="14" fill="#0e0e28" opacity="0.7"/>';
  } else if (isFull) {
    /* full moon: all lit */
    svg += '<circle cx="15" cy="15" r="14" fill="#e8e4c8"/>' + craters;
  } else {
    svg += '<circle cx="15" cy="15" r="14" fill="#0e0e28"/>';
    svg += '<path d="' + litPath + '" fill="#e8e4c8"/>';
    svg += craters;
  }
  svg += '</svg>';

  moonEl.innerHTML = svg;
}

/* ════════════════════════════════════════════════════════════════════════════
   SKY — reactive time-of-day gradient — redesign.md §3.1
   Four phases, each a 3-stop (top -> mid -> bottom) gradient. Real time uses
   continuous crossfades between adjacent phases (~45min each side of sunrise/
   sunset) instead of hard-switching; the clock-hour fallback (no SKY data
   yet) just snaps to the nearest bucket.
   ════════════════════════════════════════════════════════════════════════════ */
const SKY_PHASES = {
  dawn:  ['#f6c99f', '#e8a6a0', '#cfa3c4'],
  day:   ['#a9d4f0', '#cfe8f5', '#eaf6fb'],
  dusk:  ['#6a5a8f', '#c17b8f', '#f0a878'],
  night: ['#141a2e', '#1e2745', '#2a3358'],
};

function _hexToRgb(hex) {
  var n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function _lerpColor(a, b, t) {
  var ca = _hexToRgb(a), cb = _hexToRgb(b);
  return 'rgb(' +
    Math.round(ca[0] + (cb[0] - ca[0]) * t) + ',' +
    Math.round(ca[1] + (cb[1] - ca[1]) * t) + ',' +
    Math.round(ca[2] + (cb[2] - ca[2]) * t) + ')';
}
function _lerpPalette(pa, pb, t) {
  return [0, 1, 2].map(function (i) { return _lerpColor(pa[i], pb[i], t); });
}

/** ~45min crossfade on each side of sunrise/sunset; flat day/night in between.
 *  Returns 3 CSS color stops. */
function _skyGradientStops(nowSec, sunrise, sunset) {
  var HALF = 45 * 60;
  var dawnStart = sunrise - HALF, dawnEnd = sunrise + HALF;
  var duskStart = sunset  - HALF, duskEnd = sunset  + HALF;

  if (nowSec >= dawnStart && nowSec <= sunrise) {
    return _lerpPalette(SKY_PHASES.night, SKY_PHASES.dawn, (nowSec - dawnStart) / HALF);
  }
  if (nowSec > sunrise && nowSec <= dawnEnd) {
    return _lerpPalette(SKY_PHASES.dawn, SKY_PHASES.day, (nowSec - sunrise) / HALF);
  }
  if (nowSec > dawnEnd && nowSec < duskStart) {
    return SKY_PHASES.day;
  }
  if (nowSec >= duskStart && nowSec <= sunset) {
    return _lerpPalette(SKY_PHASES.day, SKY_PHASES.dusk, (nowSec - duskStart) / HALF);
  }
  if (nowSec > sunset && nowSec <= duskEnd) {
    return _lerpPalette(SKY_PHASES.dusk, SKY_PHASES.night, (nowSec - sunset) / HALF);
  }
  return SKY_PHASES.night;
}

/** Discrete phase label (for sun glow color + firefly trigger), using the
 *  same windows as _skyGradientStops so the two never disagree. */
function _skyPhaseLabel(nowSec, sunrise, sunset) {
  var HALF = 45 * 60;
  if (nowSec >= sunrise - HALF && nowSec <= sunrise + HALF) return 'dawn';
  if (nowSec >= sunset  - HALF && nowSec <= sunset  + HALF) return 'dusk';
  if (nowSec > sunrise + HALF && nowSec < sunset - HALF)    return 'day';
  return 'night';
}

/** Weather-condition bucket driving the overlay layer (redesign.md §3.1
 *  Layer 2): 'clear' | 'partly-cloudy' | 'overcast' | 'rain'. Prefers the
 *  live "right now" feed; falls back to the humidity/pressure proxy used
 *  before live weather was available. */
function _skyCondition(current, hum, pres) {
  if (current) {
    if (current.is_raining) return 'rain';
    var cc = current.cloud_cover_pct;
    if (cc != null) {
      if (cc >= 85) return 'overcast';
      if (cc >= 25) return 'partly-cloudy';
      return 'clear';
    }
    if (current.weather_code === 3) return 'overcast';
    if (current.weather_code === 2) return 'partly-cloudy';
    if (current.weather_code === 0 || current.weather_code === 1) return 'clear';
  }
  var cf = 0.75;
  if (hum  != null && hum  > 70)   cf += (hum - 70)   / 60;
  if (pres != null && pres < 1010) cf += (1010 - pres) / 35;
  cf = Math.min(2.0, cf);
  if (cf > 1.5) return 'overcast';
  if (cf > 1.0) return 'partly-cloudy';
  return 'clear';
}

/* WMO codes for fog/freezing fog and snow -- mirrors _RAIN_CODES/_HEAVY_CODES
 * in garden/agent/weather.py (kept in sync manually; small fixed sets). */
const _FOG_CODES  = new Set([45, 48]);
const _SNOW_CODES = new Set([71, 73, 75, 77, 85, 86]);

/** Extended weather "mood" for the full-page sky backdrop -- adds
 *  storm/fog/snow on top of the clear/partly-cloudy/overcast/rain buckets
 *  `_skyCondition` already produces for the bed rain-sheen + chips. Written
 *  onto <html data-weather> so sky-backdrop.css can react per-mood. */
function _skyWeatherMood(current, cond) {
  if (current) {
    var code = current.weather_code;
    if (_SNOW_CODES.has(code)) return 'snow';
    if (_FOG_CODES.has(code))  return 'fog';
    if (cond === 'rain' && current.intensity === 'heavy') return 'storm';
  }
  return cond;
}

/* ════════════════════════════════════════════════════════════════════════════
   GARDEN — RENDER BEDS  (called once on load)
   ════════════════════════════════════════════════════════════════════════════ */

function renderBeds() {
  const container = document.getElementById('garden-beds');
  if (!container) return;
  container.innerHTML = '';
  _initClouds();
  _initGrassBlades();
  _initGroundDressing();

  BEDS.forEach(function (bed) {
    const rng = mkRng(strHash(bed.id));

    /* ── outer bed card ── */
    const bedEl = document.createElement('div');
    bedEl.className = 'g-bed';
    bedEl.dataset.bed = bed.id;

    /* bed name */
    const nameEl = document.createElement('div');
    nameEl.className = 'g-bed-name';
    nameEl.textContent = bed.name;
    bedEl.appendChild(nameEl);

    /* ── wooden frame ── */
    const frameEl = document.createElement('div');
    frameEl.className = 'g-bed-frame';

    /* ── soil ── */
    const soilEl = document.createElement('div');
    soilEl.className = 'g-soil';
    soilEl.id = 'soil-' + bed.id;

    /* Seeded soil noise -- separate RNG so plant positions are unchanged */
    (function () {
      var sr = mkRng(strHash(bed.id + '-soil'));
      var ns = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      ns.setAttribute('aria-hidden', 'true');
      ns.setAttribute('viewBox', '0 0 100 100');
      ns.setAttribute('preserveAspectRatio', 'none');
      ns.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;';
      /* dark clods -- calm, subtle texture (redesign.md §3.2), not speckled dirt */
      var g1 = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g1.setAttribute('fill', '#2e1d10');
      g1.setAttribute('opacity', '0.22');
      for (var nc = 0; nc < 8; nc++) {
        var c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        c.setAttribute('cx', (sr() * 88 + 4).toFixed(1));
        c.setAttribute('cy', (sr() * 78 + 8).toFixed(1));
        c.setAttribute('r',  (sr() * 2.2 + 1.4).toFixed(1));
        g1.appendChild(c);
      }
      /* lighter flecks */
      var g2 = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g2.setAttribute('fill', '#7a5535');
      g2.setAttribute('opacity', '0.16');
      for (var nf = 0; nf < 4; nf++) {
        var f = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        f.setAttribute('cx', (sr() * 88 + 4).toFixed(1));
        f.setAttribute('cy', (sr() * 78 + 8).toFixed(1));
        f.setAttribute('r',  (sr() * 1.4 + 0.9).toFixed(1));
        g2.appendChild(f);
      }
      ns.appendChild(g1);
      ns.appendChild(g2);
      soilEl.appendChild(ns);
    }());

    /* ── plants ── */
    const plantsEl = document.createElement('div');
    plantsEl.className = 'g-plants';

    const n = bed.plants.length;
    /* grid layout: beds with more than 4 plants wrap into a near-square grid
       (e.g. 6 plants -> 2 rows x 3 cols, 12 plants -> 3 rows x 4 cols)
       instead of one cramped single row. A bed can force a single column
       (stacked front-to-back) via layout: vertical in config.yaml. */
    const vertical = bed.layout === 'vertical';
    const cols = vertical ? 1 : (n > 3 ? Math.ceil(n / Math.round(Math.sqrt(n))) : n);
    const rows = vertical ? n : (n > 3 ? Math.ceil(n / cols) : 1);
    /* base plant size scales down with column count, capped tighter as the
       bed gets denser overall so many-plant beds don't feel congested */
    const sizeCap = n <= 6 ? 44 : (n <= 9 ? 34 : 26);
    const pw = Math.max(18, Math.min(sizeCap, Math.floor(130 / cols)));
    const ph = Math.round(pw * 1.27);
    bed.plants.forEach(function (type, i) {
      const plantEl = document.createElement('div');
      plantEl.className = 'g-plant';
      plantEl.dataset.type = type;   /* stable emoji lookup even when sprite goes dormant */

      const row = Math.floor(i / cols);
      const col = i % cols;

      /* seeded jitter */
      const jitterRange = pw * 0.35;
      const jx    = (rng() - 0.5) * jitterRange;
      const jy    = (rng() - 0.5) * 6;
      const rot   = (rng() - 0.5) * 7;
      /* depth: 0=front/near, 1=back/far -- 5th rng call, after jitter.
         With multiple rows, the row sets the base depth (back rows are
         farther/smaller/higher) and a small jitter varies it per plant. */
      let depth = rows > 1 ? (1 - row / (rows - 1)) * 0.6 + 0.2 : rng();
      if (rows > 1) depth = Math.min(1, Math.max(0, depth + (rng() - 0.5) * 0.12));

      /* Idle sway (redesign.md §3.2): each plant gets its own gentle amplitude
         (4-8deg) and period (3.5-6s), staggered so a bed never sways in
         unison. Drawn after depth on purpose -- these calls don't affect the
         jitter/depth sequence above. */
      const swayAmp = 4 + rng() * 4;
      const swayDur = 3.5 + rng() * 2.5;
      const swayDelay = rng() * swayDur;

      /* depth + per-type meta combine for size: near/far AND species height rhythm */
      const depthScale = 1 + (0.5 - depth) * 0.22;
      const meta = PLANT_META[type] || PLANT_META.unknown;
      const dpw = Math.round(pw * depthScale * meta.w);
      const dph = Math.round(ph * depthScale * meta.h);
      plantEl.style.width  = dpw + 'px';
      plantEl.style.height = dph + 'px';

      /* x-position: evenly spaced across columns, capped so plants don't overflow.
         Vertical (single-column) layouts zigzag left/right by row so a large
         front plant doesn't sit directly in front of / hide the one behind it. */
      let frac;
      if (vertical)        frac = row % 2 === 0 ? 0.36 : 0.64;
      else if (cols === 1) frac = 0.5;
      else if (cols === 2) frac = 0.3 + col * 0.4;
      else                 frac = 0.15 + (col / (cols - 1)) * 0.7;

      plantEl.style.setProperty('--g-rot',       rot + 'deg');
      plantEl.style.setProperty('--g-sway-amp',  swayAmp.toFixed(2) + 'deg');
      plantEl.style.setProperty('--g-sway-dur',  swayDur.toFixed(2) + 's');
      plantEl.style.setProperty('--g-delay',     swayDelay.toFixed(2) + 's');
      plantEl.style.setProperty('--g-wilt-rot',  '0deg');
      plantEl.style.setProperty('--g-sat',       '1');
      plantEl.style.setProperty('--g-bright',    '1');

      /* row base: front row sits low in the bed, back rows stack upward with
         real separation so multi-row beds don't collapse into one visual row.
         Spacing per row tightens as row count grows so tall rows (3+) still
         fit inside the soil box without clipping. */
      const rowSpacing = rows <= 2 ? 46 : (rows === 3 ? 32 : 24);
      const rowBase = rows > 1 ? 8 + (rows - 1 - row) * rowSpacing : 4 + depth * 10;

      /* position: near plants sit lower (small bottom), far ones higher */
      plantEl.style.left   = 'calc(' + (frac * 100) + '% + ' + jx + 'px - ' + (dpw / 2) + 'px)';
      plantEl.style.bottom = Math.max(0, rowBase + jy * 0.3) + 'px';
      /* near = higher z so they overlap far plants */
      plantEl.style.zIndex = Math.round((1 - depth) * 8) + 1;

      /* damp soil patch (sized/faded by moisture in updateGarden) */
      const dampEl = document.createElement('div');
      dampEl.className = 'g-damp-patch';
      dampEl.id = 'damp-' + bed.id + '-' + i;
      const dBaseW = Math.round(dpw * 0.90);
      const dBaseH = Math.round(dpw * 0.36);
      dampEl.dataset.baseW = dBaseW;
      dampEl.dataset.baseH = dBaseH;
      dampEl.style.width  = dBaseW + 'px';
      dampEl.style.height = dBaseH + 'px';
      plantEl.appendChild(dampEl);

      /* rooted base mound */
      const baseEl = document.createElement('div');
      baseEl.className = 'g-plant-base';
      baseEl.style.width  = Math.round(dpw * 0.56) + 'px';
      baseEl.style.height = Math.round(dpw * 0.22) + 'px';
      plantEl.appendChild(baseEl);

      const inner = document.createElement('div');
      inner.className = 'g-plant-inner';
      inner.innerHTML =
        '<svg aria-hidden="true" focusable="false" viewBox="0 0 40 52" width="' + dpw + '" height="' + dph + '">' +
        '<use href="' + spriteHref(type) + '"/>' +
        '</svg>';
      plantEl.appendChild(inner);
      plantsEl.appendChild(plantEl);
    });

    soilEl.appendChild(plantsEl);
    frameEl.appendChild(soilEl);

    /* moisture gauge stake: slim fill bar, height/color set live in updateGarden
       from BANDS.moistureBands (per-bed optimal range) -- no ticks/text, hero
       stays number-free by design. */
    const stakeEl = document.createElement('div');
    stakeEl.className = 'g-stake';
    stakeEl.id = 'stake-' + bed.id;
    stakeEl.setAttribute('aria-hidden', 'true');
    const stakeFillEl = document.createElement('div');
    stakeFillEl.className = 'g-stake-fill';
    stakeEl.appendChild(stakeFillEl);
    frameEl.appendChild(stakeEl);

    /* front rail: overlaps the soil's bottom edge, plants appear inside the bed */
    const railEl = document.createElement('div');
    railEl.className = 'g-front-rail';
    frameEl.appendChild(railEl);

    /* No moisture%/battery badge here by design (redesign.md §3.2/§10) -- that
       data lives in the bed chip row and its inline detail; the hero stays number-free. */

    /* sleep overlay: soft z z Z + quiet nosig line, revealed when g-asleep */
    const sleepEl = document.createElement('div');
    sleepEl.className = 'g-sleep-overlay';
    sleepEl.setAttribute('aria-hidden', 'true');
    const zzzEl = document.createElement('div');
    zzzEl.className = 'g-zzz';
    zzzEl.textContent = 'z z Z';
    const nosigEl = document.createElement('div');
    nosigEl.className = 'g-nosig';
    nosigEl.id = 'nosig-' + bed.id;
    nosigEl.textContent = 'no signal';
    sleepEl.appendChild(zzzEl);
    sleepEl.appendChild(nosigEl);
    frameEl.appendChild(sleepEl);

    bedEl.appendChild(frameEl);

    container.appendChild(bedEl);
  });
}

/** Clouds now live in the full-page #sky-backdrop's .sky-band (promoted
 *  from the old 86px .garden-sky strip so the whole scene reads as one
 *  sky). Top offsets are % of the band so they stay proportioned across
 *  the taller viewport-driven height. */
function _initClouds() {
  const band = document.getElementById('sky-band');
  if (!band) return;
  band.querySelectorAll('.sky-cloud').forEach(function (c) { c.remove(); });
  /* Durations within the 40-90s "slow and few" motion budget (redesign.md §3.1) */
  [
    { left: -100, top: 6,  width: 150, dur: 70, delay: 0 },
    { left: 300,  top: 14, width: 110, dur: 88, delay: -34 },
    { left: 620,  top: 3,  width: 190, dur: 78, delay: -58 },
  ].forEach(function (c) {
    const el = document.createElement('div');
    el.className = 'sky-cloud';
    el.style.cssText = 'left:' + c.left + 'px;top:' + c.top + '%;width:' + c.width + 'px;';
    el.style.setProperty('--g-cdur',   c.dur + 's');
    el.style.setProperty('--g-cdelay', c.delay + 's');
    band.appendChild(el);
  });
}

function _initGrassBlades() {
  const horizon = document.getElementById('garden-horizon');
  if (!horizon) return;
  const old = horizon.querySelector('.g-grass-svg');
  if (old) old.remove();

  /* fixed-seed RNG: same blade layout every render */
  const rng = mkRng(0x9a3c71);
  const ns = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  ns.setAttribute('aria-hidden', 'true');
  ns.setAttribute('viewBox', '0 0 400 24');
  ns.setAttribute('preserveAspectRatio', 'none');
  ns.className = 'g-grass-svg';
  /* extends a few px above the 14px strip so blades poke up into the sky
     seam like real grass, rather than stopping in a hard flat line */
  ns.style.cssText =
    'position:absolute;left:0;right:0;bottom:0;width:100%;height:24px;pointer-events:none;z-index:2;overflow:visible;';

  const blades = ['#7fbf58', '#6fa64c', '#588a3a', '#4f7d34'];
  const count = 1200;
  /* Perf: animating all 1200 blades independently forces continuous repaint
     of the whole strip every frame (SVG sub-elements don't get the same
     cheap compositor-layer promotion HTML transforms get). Real grass also
     doesn't ripple blade-by-blade independently -- nearby blades move
     together in gusts. So only every 4th blade sways; the rest render as a
     single static <g> (tilt baked into the SVG transform attribute, no CSS
     animation, no extra wrapper element) -- full visual density, ~4x fewer
     concurrently-animated nodes. */
  const animateEvery = 4;
  for (let i = 0; i < count; i++) {
    const x     = (i / count) * 400 + (rng() - 0.5) * 0.27;
    const h     = 8 + rng() * 10;             /* blade height */
    const bend1 = (rng() - 0.5) * 9;          /* first S-curve control point */
    const bend2 = (rng() - 0.5) * 9;          /* second, opposite-leaning */
    const tilt  = (rng() - 0.5) * 6;          /* resting tilt, not all vertical */
    const w     = 0.5 + rng() * 0.45;         /* sleek, thin blades */

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    /* cubic S-curve: two opposing control points give each blade a natural
       wavy lean instead of a single uniform bend */
    path.setAttribute('d',
      'M0,0 C' + bend1.toFixed(1) + ',' + (-h * 0.35).toFixed(1) +
      ' ' + bend2.toFixed(1) + ',' + (-h * 0.7).toFixed(1) +
      ' ' + ((bend1 + bend2) * 0.15).toFixed(1) + ',' + (-h).toFixed(1));
    path.setAttribute('stroke', blades[i % blades.length]);
    path.setAttribute('stroke-width', w.toFixed(2));
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke-linecap', 'round');

    if (i % animateEvery === 0) {
      /* outer <g>: static translate positioning (an SVG "transform" attribute,
         untouched by CSS so the animated inner <g> below can own the CSS
         "transform" property without clobbering this placement) */
      const outer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      outer.setAttribute('transform', 'translate(' + x.toFixed(1) + ',24)');

      /* inner <g>: CSS-animated sway, staggered per blade like plant sway */
      const inner = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      inner.setAttribute('class', 'g-grass-blade');
      inner.style.cssText = 'transform-origin:0px 0px;';
      inner.style.setProperty('--g-blade-tilt', tilt.toFixed(1) + 'deg');
      inner.style.setProperty('--g-blade-amp',  (3 + rng() * 4).toFixed(1) + 'deg');
      inner.style.animationDuration = (2 + rng() * 2.2).toFixed(2) + 's';
      inner.style.animationDelay    = (-rng() * 3).toFixed(2) + 's';

      inner.appendChild(path);
      outer.appendChild(inner);
      ns.appendChild(outer);
    } else {
      /* static blade: single <g>, tilt baked into the transform attribute,
         no CSS animation at all */
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.setAttribute('transform',
        'translate(' + x.toFixed(1) + ',24) rotate(' + tilt.toFixed(1) + ')');
      g.appendChild(path);
      ns.appendChild(g);
    }
  }
  horizon.appendChild(ns);
}

function _initGroundDressing() {
  const ground = document.getElementById('garden-beds');
  if (!ground) return;
  /* remove any previous layer */
  const old = ground.querySelector('.g-ground-svg');
  if (old) old.remove();

  /* fixed-seed RNG: same pebble layout every render regardless of bed config */
  const rng = mkRng(0x5a7de1);
  const ns = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  ns.setAttribute('aria-hidden', 'true');
  ns.setAttribute('viewBox', '0 0 100 100');
  ns.setAttribute('preserveAspectRatio', 'none');
  ns.className = 'g-ground-svg';
  ns.style.cssText =
    'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:0;overflow:visible;';

  /* earthy pebble/stone colors */
  var stones = ['#7a6448', '#5e4c38', '#8a7458', '#6e5840'];
  for (var p = 0; p < 14; p++) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', 'ellipse');
    var cx = (rng() * 92 + 4).toFixed(1);
    var cy = (rng() * 72 + 8).toFixed(1);
    el.setAttribute('cx', cx);
    el.setAttribute('cy', cy);
    el.setAttribute('rx', (rng() * 2.0 + 1.0).toFixed(1));
    el.setAttribute('ry', (rng() * 1.2 + 0.6).toFixed(1));
    el.setAttribute('fill', stones[p % stones.length]);
    el.setAttribute('opacity', (0.30 + rng() * 0.28).toFixed(2));
    el.setAttribute('transform',
      'rotate(' + (rng() * 70 - 35).toFixed(0) + ' ' + cx + ' ' + cy + ')');
    ns.appendChild(el);
  }
  ground.appendChild(ns);
}

/* ════════════════════════════════════════════════════════════════════════════
   GARDEN — DATA HELPERS
   ════════════════════════════════════════════════════════════════════════════ */

function parseLatest(rows) {
  const readings = {}, ts = {};
  let lastUpdated = null;
  rows.forEach(function (r) {
    readings[r.sensor_key] = r.value;
    ts[r.sensor_key]       = r.ts;
    if (!lastUpdated || r.ts > lastUpdated) lastUpdated = r.ts;
  });
  return { readings: readings, ts: ts, lastUpdated: lastUpdated };
}

function isStale(isoTs) {
  if (!isoTs) return true;
  return (Date.now() - new Date(isoTs).getTime()) / 60000 > G.staleMin;
}

function _minutesAgo(isoTs) {
  if (!isoTs) return null;
  return Math.round((Date.now() - new Date(isoTs).getTime()) / 60000);
}

/* ════════════════════════════════════════════════════════════════════════════
   GARDEN — UPDATE  (called each refresh with the /api/latest rows)
   ════════════════════════════════════════════════════════════════════════════ */

const _prevDry = {};

/** Set a bed's gauge-stake fill height and status color.
 *  moist: 0-100 or null. Status is dry/ok/wet against BANDS.moistureBands[moistKey]
 *  (that bed's crop-derived optimal window); if no band exists for this sensor,
 *  falls back to the global G.dry cutoff (dry vs. ok, no "wet" distinction). */
function _updateStake(bed, moistKey, moist) {
  const stakeEl = document.getElementById('stake-' + bed.id);
  if (!stakeEl) return;
  const fillEl = stakeEl.querySelector('.g-stake-fill');
  if (!fillEl || moist == null) return;

  fillEl.style.height = Math.max(0, Math.min(100, moist)) + '%';

  const band = BANDS.moistureBands && BANDS.moistureBands[moistKey];
  let status;
  if (band) {
    status = moist < band.min ? 'dry' : (moist > band.max ? 'wet' : 'ok');
  } else {
    status = moist < G.dry ? 'dry' : 'ok';
  }
  stakeEl.classList.remove('g-stake--dry', 'g-stake--ok', 'g-stake--wet');
  stakeEl.classList.add('g-stake--' + status);
}

function updateGarden(rows) {
  const { readings, ts } = parseLatest(rows);
  const timeOfDay = tod();

  _updateSky(readings, ts, timeOfDay);

  BEDS.forEach(function (bed) {
    const moistKey = bed.sensors.soil_moisture;
    const moist    = readings[moistKey];
    const moistTs  = ts[moistKey];
    const stale    = isStale(moistTs);

    const bedEl = document.querySelector('[data-bed="' + bed.id + '"]');
    if (!bedEl) return;

    /* ── Stale / sleeping state ── */
    if (stale) {
      bedEl.classList.add('g-asleep');
      /* dormant plants: swap to leafy mound sprite, remove wilt styling */
      bedEl.querySelectorAll('.g-plant-inner use').forEach(function (u) {
        if (!u.hasAttribute('data-dormant')) {
          u.dataset.originalHref = u.getAttribute('href');
          u.setAttribute('href', '#sprite-dormant');
          u.setAttribute('data-dormant', '1');
        }
      });
      bedEl.querySelectorAll('.g-plant').forEach(function (p) {
        p.style.setProperty('--g-wilt-rot', '0deg');
        p.style.setProperty('--g-sat',      '1');
        p.style.setProperty('--g-bright',   '1');
      });
      bedEl.querySelectorAll('.g-damp-patch').forEach(function (d) {
        d.style.width   = '10px';
        d.style.height  = '4px';
        d.style.opacity = '0.12';
      });
      const staleStakeEl = document.getElementById('stake-' + bed.id);
      if (staleStakeEl) staleStakeEl.classList.add('g-stake--asleep');
      /* populate nosig text with time since last reading */
      const minsAgo = _minutesAgo(moistTs);
      const nosig = document.getElementById('nosig-' + bed.id);
      if (nosig) {
        nosig.textContent = minsAgo != null
          ? 'no signal · ' + minsAgo + 'm ago'
          : 'no signal';
      }
      return;
    }

    /* ── Waking up: restore original plant sprites if bed was asleep ── */
    bedEl.querySelectorAll('.g-plant-inner use[data-dormant]').forEach(function (u) {
      u.setAttribute('href', u.dataset.originalHref || '');
      u.removeAttribute('data-dormant');
    });
    bedEl.classList.remove('g-asleep');
    const wokeStakeEl = document.getElementById('stake-' + bed.id);
    if (wokeStakeEl) wokeStakeEl.classList.remove('g-stake--asleep');

    /* ── Soil gradient (dry = pale/sandy, wet = dark/rich) ── */
    const soilEl = document.getElementById('soil-' + bed.id);
    if (soilEl && moist != null) {
      const [topC, botC] = moistToSoilGradient(moist);
      soilEl.style.background = 'linear-gradient(to bottom,' + topC + ',' + botC + ')';
    }

    /* ── Moisture gauge stake: height = raw %, color = status vs. this bed's
       optimal band (BANDS.moistureBands), falling back to the global dry
       threshold when the bed has no crop-derived range. ── */
    _updateStake(bed, moistKey, moist);

    /* ── Damp patches: grow and darken with moisture ── */
    if (moist != null) {
      const wetF = Math.min(1, (moist || 0) / 80);
      bedEl.querySelectorAll('.g-damp-patch').forEach(function (d) {
        const bw = parseFloat(d.dataset.baseW || 30);
        const bh = parseFloat(d.dataset.baseH || 12);
        const s  = 0.45 + wetF * 0.75;  /* 0.45 at dry -> 1.2 at full wet */
        d.style.width   = Math.round(bw * s) + 'px';
        d.style.height  = Math.round(bh * s) + 'px';
        d.style.opacity = (0.2 + wetF * 0.8).toFixed(2);
      });
    }

    /* ── Plant wilt (smooth CSS transition via custom props) ── */
    if (moist != null) {
      const wf      = moist < G.dry ? (G.dry - moist) / G.dry : 0;
      const wiltRot = -(wf * 18).toFixed(1);
      const sat     = (1 - wf * 0.65).toFixed(3);
      const bright  = (1 - wf * 0.15).toFixed(3);
      bedEl.querySelectorAll('.g-plant').forEach(function (p) {
        p.style.setProperty('--g-wilt-rot', wiltRot + 'deg');
        p.style.setProperty('--g-sat',      sat);
        p.style.setProperty('--g-bright',   bright);
      });

      /* one-shot droplet ping when bed first crosses dry threshold */
      const isDry = moist < G.dry;
      if (isDry && !_prevDry[bed.id]) _triggerDropletPing(bed.id);
      _prevDry[bed.id] = isDry;
    }
  });
}

/* ── Sky + weather strip update ──────────────────────────────────────────
   Sun/moon/clouds/veil/rain/mood now render in the full-page #sky-backdrop
   (.sky-band) instead of the old 86px .garden-sky strip -- the hero keeps
   `heroSky` (#garden-sky) only for the is-night toggle that grounds birds
   and darkens the horizon trees. Gradient + mood are written as CSS custom
   properties / data-attributes on <html>, mirroring the data-theme
   pattern, so sky-backdrop.css owns all the actual paint. */
function _updateSky(readings, ts, timeOfDay) {
  const band   = document.getElementById('sky-band');
  const heroSky = document.getElementById('garden-sky');
  const sun  = document.getElementById('sky-sun');
  const moon = document.getElementById('sky-moon');
  if (!band) return;

  const nowSec = Date.now() / 1000;
  let sunUp = false, moonUp = false;

  /* Fixed viewport-px anchors for the arc (see celestialPos jsdoc) -- the
     open-sky strip between the sticky header (52px) and the hero's
     grass/treeline (~150px down), not the full 60vh backdrop band. */
  const SUN_PEAK_TOP  = 56,  SUN_LOW_TOP  = 150;
  const MOON_PEAK_TOP = 50,  MOON_LOW_TOP = 150;

  if (SKY && SKY.sunrise_ts && SKY.sunset_ts) {
    /* ── Real sun position from Open-Meteo sunrise/sunset ── */
    const sunPos = celestialPos(nowSec, SKY.sunrise_ts, SKY.sunset_ts, SUN_PEAK_TOP, SUN_LOW_TOP);
    sunUp = sunPos.up;

    if (sun) {
      sun.style.left = sunPos.left_pct.toFixed(1) + '%';
      sun.style.top  = sunPos.top_px.toFixed(0)   + 'px';
    }

    /* ── Real moon position: arc across the opposite night window ── */
    let moonRise, moonSet;
    if (SKY.sunrise_ts_tomorrow) {
      /* night length ≈ tomorrow's sunrise minus today's sunset */
      const nightLen = SKY.sunrise_ts_tomorrow - SKY.sunset_ts;
      if (nowSec < SKY.sunrise_ts) {
        /* pre-dawn window: approximate yesterday's sunset by subtracting nightLen */
        moonRise = SKY.sunrise_ts - nightLen;
        moonSet  = SKY.sunrise_ts;
      } else {
        /* post-sunset window: tonight */
        moonRise = SKY.sunset_ts;
        moonSet  = SKY.sunrise_ts_tomorrow;
      }
      const moonPos = celestialPos(nowSec, moonRise, moonSet, MOON_PEAK_TOP, MOON_LOW_TOP);
      moonUp = moonPos.up && !sunUp;
      if (moon) {
        moon.style.left = moonPos.left_pct.toFixed(1) + '%';
        moon.style.top  = moonPos.top_px.toFixed(0)   + 'px';
      }
    } else {
      moonUp = !sunUp;
    }

    /* Discrete phase label (sun glow color, fireflies) -- same windows as
       the continuous gradient below, so the two never disagree. */
    timeOfDay = _skyPhaseLabel(nowSec, SKY.sunrise_ts, SKY.sunset_ts);
  } else {
    /* ── Fallback: tod() clock-hour buckets (no location data) ── */
    const showSun = (timeOfDay === 'day' || timeOfDay === 'dawn' || timeOfDay === 'dusk');
    sunUp  = showSun;
    moonUp = !showSun;
    /* leave left/top at CSS defaults */
  }

  /* ── Sky gradient + phase: written onto <html> as CSS custom properties
     and a data-attribute, mirroring the data-theme pattern in tokens.css.
     #sky-backdrop (sky-backdrop.css) reads --sky-stop-0/1/2 and reacts to
     [data-sky-phase] purely via CSS -- no direct background writes here. ── */
  const stops = (SKY && SKY.sunrise_ts && SKY.sunset_ts)
    ? _skyGradientStops(nowSec, SKY.sunrise_ts, SKY.sunset_ts)
    : (SKY_PHASES[timeOfDay] || SKY_PHASES.day);
  const html = document.documentElement;
  html.style.setProperty('--sky-stop-0', stops[0]);
  html.style.setProperty('--sky-stop-1', stops[1]);
  html.style.setProperty('--sky-stop-2', stops[2]);
  html.dataset.skyPhase = timeOfDay;
  _applyAutoTheme(timeOfDay);

  /* ── Weather overlay (Layer 2, §3.1) + extended mood (storm/fog/snow) ── */
  const hum  = readings[WEATHER_KEYS.humidity];
  const pres = readings[WEATHER_KEYS.pressure];
  const cond = _skyCondition(CURRENT, hum, pres);
  const mood = _skyWeatherMood(CURRENT, cond);
  html.dataset.weather = mood;
  const overcastLike = cond === 'overcast' || cond === 'rain';

  /* day-only warm/cool tint, applied via [data-sky-temp] in sky-backdrop.css */
  const temp = readings[WEATHER_KEYS.temp];
  let skyTemp = 'normal';
  if (timeOfDay === 'day' && temp != null && !overcastLike) {
    if      (temp > 32) skyTemp = 'hot';
    else if (temp < 5)  skyTemp = 'cold';
  }
  html.dataset.skyTemp = skyTemp;

  /* sun: hidden under overcast/rain regardless of time of day; warm tint at dawn/dusk */
  if (sun) {
    sun.style.opacity = (sunUp && !overcastLike) ? '1' : '0';
    if (timeOfDay === 'dawn' || timeOfDay === 'dusk') {
      sun.style.background = '#ff9040';
      sun.style.boxShadow  = '0 0 46px 16px rgba(255,140,40,0.55)';
    } else {
      sun.style.background = '#ffd94a';
      sun.style.boxShadow  = '0 0 46px 16px rgba(255,220,50,0.5)';
    }
  }

  /* moon: show/hide and render real phase shape (moon stays visible through
     cloud cover -- only the sun's crisp disc is spec'd to hide) */
  if (moon) {
    moon.style.opacity = moonUp ? '1' : '0';
    _setMoonPhase(moon, moonPhase(new Date()));
  }

  /* Grey veil, storm flash, and fog haze opacity are driven purely by the
     [data-weather] attribute set above (sky-backdrop.css) -- no direct
     opacity writes needed here anymore. */

  /* clouds: none when clear, 1-2 low-opacity when partly cloudy, all 3
     denser + grey-tinted when overcast/rain/storm (redesign.md §3.1 Layer 2) */
  const cloudEls = band.querySelectorAll('.sky-cloud');
  cloudEls.forEach(function (c, i) {
    if (cond === 'clear') {
      c.style.opacity = '0';
    } else if (cond === 'partly-cloudy') {
      c.style.opacity    = i < 2 ? (0.28 + i * 0.1).toFixed(2) : '0';
      c.style.background = 'rgba(255,255,255,0.85)';
    } else {
      c.style.opacity    = (0.62 + i * 0.1).toFixed(2);
      c.style.background = 'rgba(198,202,207,0.88)';
    }
  });

  /* soil sheen + rain streaks read off the same condition (updateWeatherRain
     handles the streak layer + heavy-rain particles) */
  const gardenEl = document.getElementById('garden');
  if (gardenEl) gardenEl.classList.toggle('is-raining', cond === 'rain');

  /* fireflies at night — add if not present, remove otherwise */
  if (timeOfDay === 'night') {
    if (heroSky && !heroSky.querySelector('.g-firefly')) _addFireflies();
  } else if (heroSky) {
    heroSky.querySelectorAll('.g-firefly').forEach(function (f) { f.remove(); });
  }

  /* birds only fly in daylight — ground them at night (hero strip only;
     the backdrop's own night look is driven by [data-sky-phase]) */
  if (heroSky) heroSky.classList.toggle('is-night', timeOfDay === 'night');

  /* weather value chips */
  _setText('wv-temp', temp  != null ? temp.toFixed(1)  + '°F'   : '—');
  _setText('wv-hum',  hum   != null ? hum.toFixed(0)   + '%'    : '—');
  _setText('wv-pres', pres  != null ? pres.toFixed(2)  + ' inHg': '—');
  _setText('wv-cond', CURRENT ? CURRENT.conditions : '—');
  const condEmojiEl = document.getElementById('wv-cond-emoji');
  if (condEmojiEl) condEmojiEl.textContent = _conditionsEmoji(CURRENT);

  /* live rain/snow visual — sky-band drizzle/flurries for light weather,
     full-page downpour for heavy rain */
  updateWeatherRain(CURRENT);
}

/** Pick a chip emoji for current conditions; mirrors the WMO code buckets in weather.py. */
function _conditionsEmoji(current) {
  if (!current) return '🌤️';
  if (current.is_raining) return current.intensity === 'heavy' ? '⛈️' : '🌧️';
  const code = current.weather_code;
  if (code === 0 || code === 1) return '☀️';
  if (code === 2)  return '⛅';
  if (code === 3)  return '☁️';
  if (code === 45 || code === 48) return '🌫️';
  if (code === 71 || code === 73 || code === 75) return '❄️';
  return '🌤️';
}

function _addFireflies() {
  const ground = document.getElementById('garden-ground');
  if (!ground) return;
  const positions = [
    { left: 12, bottom: 40, dur: 2.1, delay: 0,    fx:  4, fy: -5 },
    { left: 48, bottom: 60, dur: 2.8, delay: -0.8, fx: -3, fy: -6 },
    { left: 75, bottom: 35, dur: 1.9, delay: -1.5, fx:  5, fy: -4 },
  ];
  positions.forEach(function (p) {
    const el = document.createElement('span');
    el.className = 'g-firefly';
    el.setAttribute('aria-hidden', 'true');
    el.textContent = '✨';
    el.style.cssText =
      'position:absolute;font-size:10px;left:' + p.left + '%;bottom:' + p.bottom + 'px;' +
      'pointer-events:none;z-index:5;';
    el.style.setProperty('--g-fdur',   p.dur + 's');
    el.style.setProperty('--g-fdelay', p.delay + 's');
    el.style.setProperty('--g-fx',     p.fx + 'px');
    el.style.setProperty('--g-fy',     p.fy + 'px');
    ground.appendChild(el);
  });
}

function _triggerDropletPing(bedId) {
  const frame = document.querySelector('[data-bed="' + bedId + '"] .g-bed-frame');
  if (!frame) return;
  const ping = document.createElement('span');
  ping.className = 'g-droplet-ping';
  ping.setAttribute('aria-hidden', 'true');
  ping.textContent = '💧';
  frame.appendChild(ping);
  setTimeout(function () { ping.remove(); }, 2200);
}

function _setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

/* ════════════════════════════════════════════════════════════════════════════
   THEME
   ════════════════════════════════════════════════════════════════════════════ */

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartThemeColors() {
  return {
    grid:          cssVar('--hairline'),
    ticks:         cssVar('--text-muted'),
    tooltipBg:     cssVar('--surface'),
    tooltipBorder: cssVar('--hairline'),
    tooltipTitle:  cssVar('--text-muted'),
    tooltipBody:   cssVar('--text'),
  };
}

function applyChartTheme() {
  const c = chartThemeColors();
  Object.values(instances).forEach(function (chart) {
    Object.keys(chart.options.scales).forEach(function (axis) {
      var scale = chart.options.scales[axis];
      if (scale.grid  && scale.grid.color  !== undefined) scale.grid.color  = c.grid;
      if (scale.ticks && scale.ticks.color !== undefined) scale.ticks.color = c.ticks;
    });
    const tip = chart.options.plugins.tooltip;
    tip.backgroundColor = c.tooltipBg;
    tip.borderColor     = c.tooltipBorder;
    tip.titleColor      = c.tooltipTitle;
    tip.bodyColor       = c.tooltipBody;
    chart.update('none');
  });
}

/** Card/chrome theme now follows the sky automatically -- there's no manual
 *  toggle (the sky already shows day vs. night; a separate user-picked
 *  chrome theme just fought it). Called from _updateSky() with the same
 *  timeOfDay phase driving the sky gradient, so they can never disagree. */
function _applyAutoTheme(timeOfDay) {
  const html = document.documentElement;
  const next = timeOfDay === 'night' ? 'dark' : 'light';
  if (html.dataset.theme === next) return;
  html.dataset.theme = next;
  applyChartTheme();
}

/* ════════════════════════════════════════════════════════════════════════════
   CHART FACTORY
   ════════════════════════════════════════════════════════════════════════════ */

/* wateringEmoji plugin: marks each detected watering spike with a vertical
   dashed line (top of chart down to the point) plus a "💦 time +X%" label,
   instead of a bare droplet -- reads as one labeled event, not scattered
   points. chart._wateringEvents : [{idx, deltaPct, timeLabel}]. */
Chart.register({
  id: 'wateringEmoji',
  afterDraw(chart) {
    const events = chart._wateringEvents;
    if (!events || !events.length) return;
    const meta   = chart.getDatasetMeta(0);
    const yScale = chart.scales.y;
    const ctx    = chart.ctx;
    ctx.save();
    events.forEach(function (ev) {
      const pt = meta.data[ev.idx];
      if (!pt || !yScale) return;

      ctx.beginPath();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = 'rgba(59,130,246,0.45)';
      ctx.lineWidth   = 1;
      ctx.moveTo(pt.x, yScale.top);
      ctx.lineTo(pt.x, pt.y);
      ctx.stroke();
      ctx.setLineDash([]);

      const label = '💦 ' + ev.timeLabel + ' +' + ev.deltaPct.toFixed(0) + '%';
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(59,130,246,0.9)';
      ctx.fillText(label, pt.x, yScale.top + 10);
    });
    ctx.restore();
  }
});

/* thresholdBands plugin: draws filled zone boxes and dashed threshold lines behind
   the chart series. Charts set chart._bands and chart._lines before rendering.
   chart._bands : [{yMin, yMax, color}]   -- filled horizontal bands
   chart._lines : [{y, color, label}]     -- dashed horizontal lines with optional label
   chart._projection : {fromIdx, toValue, days, color} -- faint sloped dashed
     line from the last real point down to the dry threshold, drawn in afterDraw
     so it layers over the series line (a projection, not a background zone). */
Chart.register({
  id: 'thresholdBands',
  beforeDraw(chart) {
    const bands = chart._bands;
    const lines = chart._lines;
    if ((!bands || !bands.length) && (!lines || !lines.length)) return;

    const yScale = chart.scales.y;
    const xScale = chart.scales.x;
    if (!yScale || !xScale) return;

    const ctx    = chart.ctx;
    const left   = xScale.left;
    const right  = xScale.right;
    ctx.save();

    /* filled bands */
    if (bands && bands.length) {
      bands.forEach(function(b) {
        var top    = yScale.getPixelForValue(b.yMax);
        var bottom = yScale.getPixelForValue(b.yMin);
        /* clamp to chart area */
        top    = Math.max(top,    yScale.top);
        bottom = Math.min(bottom, yScale.bottom);
        if (bottom <= top) return;
        ctx.fillStyle = b.color;
        ctx.fillRect(left, top, right - left, bottom - top);
      });
    }

    /* dashed lines */
    if (lines && lines.length) {
      lines.forEach(function(l) {
        var py = yScale.getPixelForValue(l.y);
        if (py < yScale.top || py > yScale.bottom) return;
        ctx.beginPath();
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = l.color || '#ef4444';
        ctx.lineWidth   = 1.5;
        ctx.moveTo(left,  py);
        ctx.lineTo(right, py);
        ctx.stroke();
        ctx.setLineDash([]);
        /* small label at right edge */
        if (l.label) {
          ctx.fillStyle  = l.color || '#ef4444';
          ctx.font       = '10px sans-serif';
          ctx.textAlign  = 'right';
          ctx.textBaseline = 'bottom';
          ctx.fillText(l.label, right - 2, py - 2);
        }
      });
    }

    ctx.restore();
  },
  afterDraw(chart) {
    /* Drydown projection: faint dashed slope from the last real point down to
       the dry threshold, labeled "~Nd to dry". Drawn after the series line so
       it reads as a forward-looking projection, not a background zone. */
    const proj = chart._projection;
    if (!proj) return;
    const yScale = chart.scales.y;
    const meta   = chart.getDatasetMeta(0);
    const fromPt = meta.data[proj.fromIdx];
    if (!yScale || !fromPt) return;

    const toY = yScale.getPixelForValue(proj.toValue);
    const toX = fromPt.x + Math.min(chart.chartArea.right - fromPt.x, 60);

    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = proj.color || 'rgba(148,163,184,0.7)';
    ctx.lineWidth   = 1.5;
    ctx.moveTo(fromPt.x, fromPt.y);
    ctx.lineTo(toX, toY);
    ctx.stroke();
    ctx.setLineDash([]);

    if (proj.label) {
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'left';
      ctx.fillStyle = proj.color || 'rgba(148,163,184,0.9)';
      ctx.fillText(proj.label, toX + 4, toY);
    }
    ctx.restore();
  }
});

const instances = {};

/* Cache of latest series rows keyed by sensor key.
   Populated by loadChart(); consumed by updateStats() for sparklines + deltas. */
const seriesCache = {};

/* Cache of series rows keyed by "sensorKey|hours", so switching Trends range
   back to one already viewed this session redraws instantly from cache
   instead of waiting on a fresh /api/series round trip. Populated by
   loadChart() alongside seriesCache; never evicted (payloads are small
   post-downsampling, and a session only visits a handful of ranges). */
const seriesCacheByRange = {};

/* VPD zone colors (fixed semantic; do not need to react to light/dark theme). */
var _VPD_COLORS = [
  'rgba(59,130,246,0.09)',   /* too low   -- blue   */
  'rgba(34,197,94,0.09)',    /* healthy   -- green  */
  'rgba(245,158,11,0.09)',   /* high      -- amber  */
  'rgba(239,68,68,0.09)',    /* very high -- red    */
];

/** Shared chart factory. `opts.datasets` (with `opts.legend: true`) builds a
 *  multi-series chart for the Trends grouped panels (§6); otherwise this is
 *  the plain single-series chart used everywhere else (moisture row, bed detail). */
function makeChartOpts(color, opts) {
  opts = opts || {};
  const c = chartThemeColors();
  const scales = {
    x: {
      grid:  { color: c.grid },
      ticks: { color: c.ticks, maxTicksLimit: 6, font: { size: 10 } }
    },
    y: {
      grid:  { color: c.grid },
      ticks: { color: c.ticks, font: { size: 10 } },
      /* Headroom so the watering-event label (drawn just under the top edge
         by the wateringEmoji plugin) never clips a data point near the max. */
      grace: '8%'
    }
  };
  if (opts.dualAxis) {
    scales.y1 = {
      position: 'right',
      grid:  { drawOnChartArea: false },
      ticks: { color: c.ticks, font: { size: 10 } }
    };
  }
  return {
    type: 'line',
    data: {
      labels: [],
      datasets: opts.datasets || [{
        data: [],
        borderColor: color,
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
        fill: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: !!opts.legend, position: 'top', labels: { color: c.ticks, boxWidth: 10, font: { size: 10 } } },
        tooltip: {
          mode: 'index',
          intersect: false,
          backgroundColor:  c.tooltipBg,
          borderColor:      c.tooltipBorder,
          borderWidth:      1,
          titleColor:       c.tooltipTitle,
          bodyColor:        c.tooltipBody,
          padding:          8,
          callbacks: { label: function (ctx) { return ' ' + (ctx.dataset.label ? ctx.dataset.label + ': ' : '') + parseFloat(ctx.parsed.y).toFixed(1); } }
        }
      },
      scales: scales
    }
  };
}

/* ════════════════════════════════════════════════════════════════════════════
   DATA FETCH
   ════════════════════════════════════════════════════════════════════════════ */

/** Format a UTC ISO timestamp for chart axes, matching the header clock's 24h
 *  format. Ranges over a day (7d) also carry a date, since "HH:MM" alone
 *  repeats every day and reads as duplicated/out-of-order ticks otherwise. */
function fmtTime(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  if (HOURS > 24) return d.toLocaleDateString([], { month: 'short', day: '2-digit' }) + ' ' + time;
  return time;
}

/* Per-key request counters so a slow, superseded /api/series response can never
 * overwrite a chart with stale data that arrived out of order (the source of
 * the duplicated/out-of-order tick-label bug). */
const _seriesReqSeq = {};

/** Draws/updates the chart for `key` from already-fetched `rows`, and caches
 *  them (both the sensor-keyed cache other readers rely on, and the
 *  range-keyed cache that makes range toggles instant). Split out of
 *  loadChart() so a range switch can render synchronously from
 *  seriesCacheByRange before the network round trip resolves. */
function _renderChart(key, color, rows) {
  const labels = rows.map(function (r) { return fmtTime(r.ts); });
  const data   = rows.map(function (r) { return r.value; });

  /* Cache rows for KPI sparklines and delta computation, and for instant
     redraw next time this exact (sensor, range) pair is selected. */
  seriesCache[key] = rows;
  seriesCacheByRange[key + '|' + HOURS] = rows;

  /* Detect watering spikes (>=10% point-to-point jump) and label each one with
     its time + magnitude, drawn by the wateringEmoji plugin as a vertical
     marker instead of a bare droplet per point. */
  var wateringEvents = [];
  if (key.startsWith('soilmoisture') && data.length > 1) {
    for (var i = 1; i < data.length; i++) {
      var delta = data[i] - data[i - 1];
      if (delta >= 10) wateringEvents.push({ idx: i, deltaPct: delta, timeLabel: labels[i] });
    }
  }

  /* Build threshold bands and lines for moisture and VPD charts. */
  var bands = [];
  var lines = [];

  var moistBand = BANDS && BANDS.moistureBands && BANDS.moistureBands[key];
  if (moistBand) {
    /* Green optimal band */
    bands.push({ yMin: moistBand.min, yMax: moistBand.max, color: 'rgba(34,197,94,0.10)' });
    /* Red dashed too-wet line at band max */
    lines.push({ y: moistBand.max, color: '#ef4444', label: 'Too wet (' + moistBand.max + '%)' });
  }

  if (key === 'vpd_kpa' && BANDS && BANDS.vpdBands && BANDS.vpdBands.length) {
    var lower = 0;
    BANDS.vpdBands.forEach(function(b, i) {
      bands.push({ yMin: lower, yMax: b.upTo, color: _VPD_COLORS[i] || 'rgba(128,128,128,0.07)' });
      lower = b.upTo;
    });
  }

  /* Drydown projection: a lightweight client-side estimate (slope over the
     tail since the last watering spike, or the whole series if none) of
     when this bed reaches its crop's dry threshold. This is a visual hint
     only -- the authoritative Theil-Sen estimate lives server-side in
     garden.derived.drydown_rate() and drives the Telegram brief instead. */
  var projection = null;
  if (moistBand && data.length >= 4) {
    var tailStart = wateringEvents.length ? wateringEvents[wateringEvents.length - 1].idx : 0;
    var tail = rows.slice(tailStart).filter(function (r) { return r.value != null; });
    if (tail.length >= 4) {
      var t0 = new Date(tail[0].ts).getTime();
      var tN = new Date(tail[tail.length - 1].ts).getTime();
      var vN = tail[tail.length - 1].value;
      var hoursSpan = (tN - t0) / 3600000;
      if (hoursSpan > 0) {
        var perHour = (tail[0].value - vN) / hoursSpan; /* positive = drying */
        if (perHour > 0.01 && vN > moistBand.min) {
          var hoursToDry = (vN - moistBand.min) / perHour;
          var daysToDry  = hoursToDry / 24;
          var projLabel  = hoursToDry < 24 ? ('~' + Math.round(hoursToDry) + 'h to dry')
                          : daysToDry >= 14 ? '2+ wks to dry'
                          : ('~' + Math.round(daysToDry) + 'd to dry');
          projection = {
            fromIdx: data.length - 1,
            toValue: moistBand.min,
            label: projLabel,
            color: 'rgba(148,163,184,0.8)'
          };
        }
      }
    }
  }

  if (instances[key]) {
    instances[key].data.labels            = labels;
    instances[key].data.datasets[0].data  = data;
    instances[key]._wateringEvents        = wateringEvents;
    instances[key]._bands                 = bands;
    instances[key]._lines                 = lines;
    instances[key]._projection            = projection;
    instances[key].update('none');
  } else {
    const canvas = document.getElementById('chart-' + key);
    if (!canvas) return;
    instances[key] = new Chart(canvas, makeChartOpts(color));
    instances[key].data.labels            = labels;
    instances[key].data.datasets[0].data  = data;
    instances[key]._wateringEvents        = wateringEvents;
    instances[key]._bands                 = bands;
    instances[key]._lines                 = lines;
    instances[key]._projection            = projection;
    instances[key].update();
  }
}

/** Fetches + draws the chart for `key` at the current HOURS. If this exact
 *  (sensor, range) pair was already fetched this session, redraws instantly
 *  from seriesCacheByRange first (synchronously, before the network call
 *  resolves) so range toggles feel instant, then still fetches in the
 *  background to bring the chart current. */
async function loadChart(key, color) {
  const cached = seriesCacheByRange[key + '|' + HOURS];
  if (cached) _renderChart(key, color, cached);

  const reqId = (_seriesReqSeq[key] = (_seriesReqSeq[key] || 0) + 1);
  const resp = await fetch('/api/series?sensor=' + encodeURIComponent(key) + '&hours=' + HOURS);
  if (!resp.ok) return;
  const rows = await resp.json();
  if (_seriesReqSeq[key] !== reqId) return; /* a newer request already landed */
  _renderChart(key, color, rows);
}

/* ════════════════════════════════════════════════════════════════════════════
   PER-BED SOIL MOISTURE ROW
   ════════════════════════════════════════════════════════════════════════════ */

/** Build the 4 per-bed moisture chart-cards (one row) from MOISTURE_GROUP.
 *  Each card's title is the bed's dominant-crop emoji + bed name; the chart
 *  itself is a single-series chart driven by the existing loadChart(), which
 *  already draws the optimal-moisture band + too-wet line from
 *  BANDS.moistureBands[key] and the 💦 watering-spike marker. */
function renderBedMoistureCards() {
  const grid = document.getElementById('bed-moisture-grid');
  if (!grid || !MOISTURE_GROUP.length) return;
  grid.innerHTML = MOISTURE_GROUP.map(function (m) {
    return (
      '<div class="chart-card">' +
        '<div class="chart-title">' + bedEmoji(m.plants) + ' ' + m.bed + ' moisture</div>' +
        '<div class="chart-wrap"><canvas id="chart-' + m.key + '"></canvas></div>' +
      '</div>'
    );
  }).join('');
}

/* ════════════════════════════════════════════════════════════════════════════
   TRENDS — grouped climate charts (region D) — redesign.md §6
   Outdoor + gazebo series share one card/axis per family instead of a card
   each, roughly halving the chart count. Charted keys come straight from the
   server-injected CHARTS list, so a config without a given sensor (e.g. no
   gazebo probe) just drops that line rather than erroring.
   ════════════════════════════════════════════════════════════════════════════ */

const TRENDS_GROUPS = [
  /* heatindex_f folds into the same line chart as outdoor/gazebo temp — same
     unit, same axis, and it's just derived from temp1_f+humidity1 anyway. */
  { id: 'temperature', title: 'Temperature',        keys: ['temp_f', 'temp1_f', 'heatindex_f'] },
  { id: 'humidity',    title: 'Humidity',            keys: ['humidity', 'humidity1'] },
  { id: 'vpd',         title: 'VPD',                 keys: ['vpd_kpa'], vpdBand: true },
  /* Pressure & dew point were dropped — neither drives a gardening decision
     on its own. This 4th slot previously held a drydown-day projection
     ("When to water next") that was too noisy to trust; renderMoistureBand
     Card() (below) replaced it with a live snapshot instead of a forecast. */
];

/** Builds the grouped climate chart cards once, then (re)draws each on every
 *  call — safe to call on every refresh tick, same pattern as the moisture row.
 *  The last card, "Soil moisture vs. band", isn't a canvas line chart — it's a
 *  live snapshot (renderMoistureBandCard) sharing the same chart-card chrome
 *  so the grid reads as one symmetric set. */
function renderTrendsClimateGrid() {
  const grid = document.getElementById('trends-climate-grid');
  if (!grid) return;

  const groups = TRENDS_GROUPS.filter(function (g) {
    return g.keys.some(function (k) { return !!_chartMeta(k); });
  });

  if (!grid.dataset.built) {
    var cardsHtml = groups.map(function (g) {
      return (
        '<div class="chart-card">' +
          '<div class="chart-title">' + g.title + '</div>' +
          '<div class="chart-wrap"><canvas id="trend-chart-' + g.id + '"></canvas></div>' +
        '</div>'
      );
    }).join('');
    cardsHtml += (
      '<div class="chart-card">' +
        '<div class="chart-title">Soil moisture vs. band</div>' +
        '<div class="moistband-forecast" id="moistband-forecast"></div>' +
      '</div>'
    );
    grid.innerHTML = cardsHtml;
    grid.dataset.built = '1';
  }

  groups.forEach(_drawTrendGroupChart);
  renderMoistureBandCard();
}

function _drawTrendGroupChart(g) {
  const canvas = document.getElementById('trend-chart-' + g.id);
  if (!canvas) return;

  const validKeys = g.keys.filter(function (k) { return !!seriesCache[k] && !!_chartMeta(k); });
  if (!validKeys.length) return;

  const labels = seriesCache[validKeys[0]].map(function (r) { return fmtTime(r.ts); });
  const datasets = validKeys.map(function (k, i) {
    const meta = _chartMeta(k);
    return {
      label:       meta.label,
      data:        seriesCache[k].map(function (r) { return r.value; }),
      borderColor: meta.color,
      borderWidth: 1.5,
      pointRadius: 0,
      tension:     0.3,
      fill:        false,
      yAxisID:     (g.dualAxis && i === 1) ? 'y1' : 'y',
    };
  });

  var bands = [];
  if (g.vpdBand && BANDS && BANDS.vpdBands && BANDS.vpdBands.length) {
    var lower = 0;
    BANDS.vpdBands.forEach(function (b, i) {
      bands.push({ yMin: lower, yMax: b.upTo, color: _VPD_COLORS[i] || 'rgba(128,128,128,0.07)' });
      lower = b.upTo;
    });
  }

  const instKey = 'trend-' + g.id;
  let chart = instances[instKey];
  if (!chart) {
    chart = new Chart(canvas, makeChartOpts(datasets[0].borderColor, {
      datasets: datasets, legend: datasets.length > 1, dualAxis: g.dualAxis,
    }));
    instances[instKey] = chart;
  }
  chart.data.labels    = labels;
  chart.data.datasets  = datasets;
  chart._bands         = bands;
  chart._lines         = [];
  chart._wateringEvents = [];
  chart._projection    = null;
  chart.update('none');
}

/* ── Time-range control + collapse (§6) ────────────────────────────────────── */

function setTrendsHours(hours) {
  if (hours === HOURS) return;
  HOURS = hours;
  document.querySelectorAll('.trends-range-btn').forEach(function (btn) {
    btn.classList.toggle('is-active', parseInt(btn.dataset.hours, 10) === hours);
  });
  reloadTrendsSeries();
}

/** Refetches every charted series at the newly selected window, then redraws
 *  the moisture row + grouped climate charts (and, incidentally, keeps every
 *  other reader of seriesCache -- climate strip, bed detail -- in sync too). */
async function reloadTrendsSeries() {
  var loads = CHARTS.map(function (c) { return loadChart(c.key, c.color); });
  loads = loads.concat(MOISTURE_GROUP.map(function (m) { return loadChart(m.key, m.color); }));
  /* loadChart() draws synchronously from seriesCacheByRange (if present)
     before its own network call yields, so this redraw already reflects any
     cached data for the newly selected range -- instant on a revisited range. */
  renderTrendsClimateGrid();
  await Promise.all(loads);
  renderTrendsClimateGrid();
}

(function initTrends() {
  document.querySelectorAll('.trends-range-btn').forEach(function (btn) {
    btn.addEventListener('click', function () { setTrendsHours(parseInt(btn.dataset.hours, 10)); });
  });
})();

/* ── Battery badge (§4) ─────────────────────────────────────────────────────
   SVG icon with proportional fill and ok/low/critical color from BANDS.battery. */
function buildBatteryBadge(volts) {
  var bc = BANDS && BANDS.battery;
  var nominal  = bc ? bc.nominal  : 1.5;
  var warn     = bc ? bc.warn     : 1.1;
  var critical = bc ? bc.critical : 0.94;
  var state = volts <= critical
    ? { color: '#ef4444', label: 'critical' }
    : volts <= warn
    ? { color: '#f59e0b', label: 'low' }
    : { color: '#22c55e', label: 'ok' };
  var pct = Math.max(0, Math.min(100, ((volts - critical) / (nominal - critical)) * 100));
  var fillW = Math.round((18 * pct) / 100);
  return (
    '<span class="battery-badge" title="' + volts.toFixed(2) + 'V (' + state.label + ')">' +
      '<svg width="26" height="14" style="vertical-align:middle">' +
        '<rect x="0" y="1" width="22" height="12" rx="2" fill="none" stroke="' + state.color + '" stroke-width="1.5"/>' +
        '<rect x="22" y="4" width="3" height="6" fill="' + state.color + '"/>' +
        '<rect x="2" y="3" width="' + fillW + '" height="8" fill="' + state.color + '"/>' +
      '</svg>' +
      '<span style="color:' + state.color + ';font-size:11px;margin-left:3px">' + volts.toFixed(2) + 'V</span>' +
    '</span>'
  );
}

/** Update the stat-strip values from a latest-rows array */
function updateStats(rows) {
  if (rows.length) {
    const latest = rows.reduce(function (a, b) { return a.ts > b.ts ? a : b; });
    _updateConnDot(latest.ts);
  } else {
    _updateConnDot(null);
  }
}

/** Connection dot: green = fresh, amber = stale (> G.staleMin old), red = no data at all. */
function _updateConnDot(isoTs) {
  const dot = document.getElementById('conn-dot');
  if (!dot) return;
  dot.classList.remove('is-fresh', 'is-stale', 'is-offline');
  if (!isoTs) {
    dot.classList.add('is-offline');
    dot.title = 'No sensor data';
    return;
  }
  const ageMin = (Date.now() - new Date(isoTs).getTime()) / 60000;
  if (ageMin > G.staleMin) {
    dot.classList.add('is-stale');
    dot.title = 'Data ' + Math.round(ageMin) + ' min old';
  } else {
    dot.classList.add('is-fresh');
    dot.title = 'Data fresh (' + _fmtLocalTs(isoTs) + ')';
  }
}

/** Live clock, ticking every second: "22:55 · Thu 03 Jul" — redesign.md §2. */
function _tickClock() {
  const el = document.getElementById('header-clock');
  if (!el) return;
  const now = new Date();
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, hourCycle: 'h23',
    hour: '2-digit', minute: '2-digit',
    weekday: 'short', day: '2-digit', month: 'short',
  });
  const p = {};
  fmt.formatToParts(now).forEach(function (part) { p[part.type] = part.value; });
  el.textContent = p.hour + ':' + p.minute + ' · ' + p.weekday + ' ' + p.day + ' ' + p.month;
}

/* ════════════════════════════════════════════════════════════════════════════
   CLIMATE STRIP (region C) — redesign.md §5
   ════════════════════════════════════════════════════════════════════════════ */

/** Change over the last ~1hr for a charted sensor key (current minus the cached
 *  reading closest to 1hr ago). Returns null when there isn't enough history yet. */
function _hourlyDelta(key, currentVal, tsIso) {
  var cached = seriesCache[key];
  if (!cached || cached.length < 2 || currentVal == null) return null;
  var nowMs    = tsIso ? new Date(tsIso).getTime() : Date.now();
  var targetMs = nowMs - 3600000;
  var best     = cached[0];
  cached.forEach(function (r) {
    if (Math.abs(new Date(r.ts).getTime() - targetMs) <
        Math.abs(new Date(best.ts).getTime() - targetMs)) { best = r; }
  });
  return currentVal - parseFloat(best.value);
}

/** Direction of change over the last ~1hr for a charted sensor key, using the
 *  same "closest reading ~1hr ago" comparison as the legacy stat-strip deltas. */
function _trendArrow(key, currentVal, tsIso) {
  var delta = _hourlyDelta(key, currentVal, tsIso);
  if (delta == null) return { arrow: '', cls: '' };
  if (delta > 0.02)  return { arrow: '▲', cls: 'is-up' };
  if (delta < -0.02) return { arrow: '▼', cls: 'is-down' };
  return { arrow: '■', cls: 'is-flat' };
}

/** What kind of number each climate stat is — shown as a hover tooltip so a
 *  first-time visitor can tell a live sensor reading from a forecast/derived
 *  figure without adding any visible clutter to the stat tiles themselves. */
const CLIMATE_STAT_SOURCE = {
  vpd:   'Derived from live temperature + humidity sensor readings',
  temp:  'Live sensor reading (gazebo station)',
  water: 'Computed from Open-Meteo forecast rainfall minus evapotranspiration — not a physical sensor',
};

function _climateStatHTML(label, value, unit, trend, sub, kind) {
  var title = CLIMATE_STAT_SOURCE[kind] || '';
  return (
    '<button type="button" class="climate-stat" onclick="scrollToTrends()"' +
      (title ? ' title="' + title + '"' : '') + '>' +
      '<span class="climate-stat-label">' + label + '</span>' +
      '<span class="climate-stat-value">' + value +
        '<span class="climate-stat-unit">' + unit + '</span>' +
        (trend.arrow ? '<span class="climate-stat-trend ' + trend.cls + '">' + trend.arrow + '</span>' : '') +
      '</span>' +
      (sub ? '<span class="climate-stat-sub">' + sub + '</span>' : '') +
    '</button>'
  );
}

/** Climate stats already summarize what Trends charts in detail below; clicking
 *  one just scrolls there instead of opening a duplicate detail view. */
function scrollToTrends() {
  const el = document.getElementById('trends');
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/** Plain-language verdict: leads with beds needing action, else affirms all-clear.
 *  No em-dashes per the house style — " · " joins clauses instead. */
function _buildVerdict(beds, current, waterBalanceIn, vpd, forecast) {
  var needsWater = (beds || []).filter(function (b) { return b.status === 'dry'; });
  var tooWet     = (beds || []).filter(function (b) { return b.status === 'wet'; });
  var stressed   = (beds || []).filter(function (b) { return b.status === 'cold' || b.status === 'heat'; });

  var lead;
  if (needsWater.length) {
    lead = needsWater.length + ' bed' + (needsWater.length > 1 ? 's' : '') + ' dry · water soon';
  } else if (tooWet.length) {
    lead = tooWet.length + ' bed' + (tooWet.length > 1 ? 's' : '') + ' too wet · hold off watering';
  } else if (stressed.length) {
    lead = stressed.length + ' bed' + (stressed.length > 1 ? 's' : '') + ' under stress · check bed detail';
  } else {
    lead = 'All beds happy';
  }

  var cond = current && current.conditions ? current.conditions.toLowerCase() : null;
  var irrigation = waterBalanceIn == null ? null
                 : waterBalanceIn > 0.05   ? 'irrigation not needed'
                 : waterBalanceIn < -0.05  ? 'irrigation recommended'
                 : 'irrigation optional';

  var parts = [lead];
  if (cond) parts.push(cond);
  if (irrigation && !needsWater.length) parts.push(irrigation);

  /* Mildew/fungal risk — surfaces vpd_status()'s existing 'low' signal in
     plain, decision-oriented language rather than raw VPD jargon. */
  if (vpd && vpd.status === 'low') parts.push('humid air, mildew risk');

  /* Rain outlook — only worth a clause when rain is actually likely soon;
     reuses the same next-12h lookahead cutoff the LLM brief uses server-side. */
  var peak = forecast && forecast.next_12h_peak_rain_pct;
  if (peak != null && peak >= (G.rainLookaheadPct || 40)) {
    var hrs = forecast.next_12h_peak_hour_offset + 1;
    parts.push('rain likely within ' + hrs + 'h');
  }

  return parts.join(' · ');
}

/** Renders the verdict sentence + 3 headline stats (VPD, outdoor temp/feels-like,
 *  water balance) from the same /api/insights payload the old insight grid used. */
function renderClimateStrip(data) {
  var verdictEl = document.getElementById('climate-verdict');
  var statsEl   = document.getElementById('climate-stats');
  if (!verdictEl || !statsEl) return;

  var wb = data.forecast && data.forecast.water_balance_in != null ? data.forecast.water_balance_in : null;
  var verdict = _buildVerdict(data.beds, data.current, wb, data.vpd, data.forecast);

  /* Stale data (redesign.md §9): a quiet inline note, not a page-wide gray-out --
     the connection dot already carries the amber/red signal. */
  if (isStale(LATEST_UPDATED_TS)) {
    var mins = _minutesAgo(LATEST_UPDATED_TS);
    verdict += mins != null ? ' · data ' + mins + 'min old' : ' · data offline';
  }
  verdictEl.textContent = verdict;

  var stats = [];

  if (data.vpd) {
    var vTrend = _trendArrow('vpd_kpa', data.vpd.value);
    var vStats = data.stats && data.stats.vpd_kpa;
    var vSub = data.vpd.label + (vStats ? ' · 24h ' + vStats.min.toFixed(2) + '–' + vStats.max.toFixed(2) : '');
    stats.push(_climateStatHTML('VPD', data.vpd.value.toFixed(2), 'kPa', vTrend, vSub, 'vpd'));
  }

  var outdoorTemp = LATEST_READINGS.temp1_f;
  if (outdoorTemp != null) {
    var tTrend = _trendArrow('temp1_f', parseFloat(outdoorTemp));
    var feels  = data.heat_index ? 'feels ' + Math.round(data.heat_index.value) + '°' : '';
    var tStats = data.stats && data.stats[WEATHER_KEYS.temp];
    var tSub = feels + (tStats ? (feels ? ' · ' : '') + '24h ' + Math.round(tStats.min) + '–' + Math.round(tStats.max) + '°' : '');
    stats.push(_climateStatHTML('Outdoor temp', Math.round(outdoorTemp), '°F', tTrend, tSub, 'temp'));
  }

  if (wb != null) {
    /* No intraday series backs this figure (one forecast-derived value per day),
       so there's no real trend to arrow — the Surplus/Deficit/Even sub-label
       carries the direction instead. */
    var wbLabel = wb > 0.05 ? 'Surplus' : wb < -0.05 ? 'Deficit' : 'Even';
    stats.push(_climateStatHTML('Water balance', (wb > 0 ? '+' : '') + wb.toFixed(2), '"', { arrow: '', cls: '' }, wbLabel, 'water'));
  }

  statsEl.innerHTML = stats.join('');
}

/* ════════════════════════════════════════════════════════════════════════════
   BED STATUS CHIPS — redesign.md §3.3
   ════════════════════════════════════════════════════════════════════════════ */

/**
 * One-word chip state per bed. Priority: Offline (sensor stale/dead) beats
 * Dry/Wet (moisture out of the crop-derived band, from /api/insights bed_stress)
 * beats Low (battery below the alert threshold but bed otherwise fine) beats OK.
 * Temperature-only stress (cold/heat) has no dedicated chip word in the spec's
 * fixed vocabulary, so it renders as OK here — the inline bed detail's verdict
 * line still surfaces it in full.
 */
function _bedChipState(bed, stress) {
  const moistTs = LATEST_TS[bed.sensors.soil_moisture];
  if (isStale(moistTs)) return { word: 'Offline', cls: 'offline' };

  if (stress) {
    if (stress.status === 'dry') return { word: 'Dry', cls: 'warn' };
    if (stress.status === 'wet') return { word: 'Wet', cls: 'warn' };
  }

  const battRaw = LATEST_READINGS[bed.sensors.soil_battery];
  const battWarn = (BANDS && BANDS.battery && BANDS.battery.warn != null) ? BANDS.battery.warn : G.battLow;
  if (battRaw != null && parseFloat(battRaw) < battWarn) return { word: 'Low', cls: 'warn' };

  return { word: 'OK', cls: 'ok' };
}

/** Moisture-level fill color for a bed chip: red when well below the
 *  crop-derived healthy band, orange when just under it, green within it,
 *  blue when above it (too wet). Falls back to a neutral gray with no band. */
function _moistureFillColor(value, band) {
  if (value == null) return 'transparent';
  if (!band) return 'var(--text-faint)';
  if (value < band.min) {
    const deficit = band.min - value;
    return deficit > 15 ? 'var(--crit)' : 'var(--warn)';
  }
  if (value > band.max) return 'var(--accent)';
  return 'var(--fill-healthy)';
}

/** Renders the 4 always-fixed-order bed chips from the latest /api/insights bed_stress. */
function renderBedChips(insightBeds) {
  const row = document.getElementById('bed-chip-row');
  if (!row) return;

  const byId = {};
  (insightBeds || []).forEach(function (b) { byId[b.id] = b; });

  row.innerHTML = BEDS.map(function (bed) {
    const state = _bedChipState(bed, byId[bed.id]);
    const isOpen = OPEN_BED === bed.id;

    const moistVal = LATEST_READINGS[bed.sensors.soil_moisture] != null
      ? parseFloat(LATEST_READINGS[bed.sensors.soil_moisture]) : null;
    const band = BANDS && BANDS.moistureBands ? BANDS.moistureBands[bed.sensors.soil_moisture] : null;
    const fillPct   = moistVal != null ? Math.max(0, Math.min(100, moistVal)) : 0;
    const fillColor = _moistureFillColor(moistVal, band);

    return (
      '<button type="button" class="bed-chip is-' + state.cls + (isOpen ? ' is-open' : '') + '" ' +
        'onclick="toggleBedDetail(\'' + bed.id + '\')" ' +
        'aria-expanded="' + isOpen + '" ' +
        'title="Soil moisture: live sensor · status: computed from crop-specific healthy range" ' +
        'aria-label="' + bed.name + ': ' + state.word + (moistVal != null ? ', ' + moistVal.toFixed(0) + '% moisture' : '') + '">' +
        '<span class="bed-chip-fill" aria-hidden="true" style="width:' + fillPct + '%; background:' + fillColor + '"></span>' +
        '<span class="bed-chip-dot" aria-hidden="true"></span>' +
        '<span class="bed-chip-name">' + bed.name + '</span>' +
        '<span class="bed-chip-state">' + state.word + '</span>' +
      '</button>'
    );
  }).join('');
}

/* ════════════════════════════════════════════════════════════════════════════
   SOIL MOISTURE VS. BAND — live per-bed position within its own dry/ok/wet
   range. Replaced the "When to water next" day-count projection, which was
   too noisy to trust (a bed's drydown rate is fragile right after it's been
   watered — see the removed garden.main._bed_watering_forecast). This card
   has no forecasting in it at all: just the current reading plotted against
   BANDS.moistureBands[key] (the same self-learned/crop-fallback band the bed
   chips and stakes already use), so it can't be wrong in the way a
   projection can — it only ever states where things stand right now.
   ════════════════════════════════════════════════════════════════════════════ */

/** How far below band.min still counts as "drying" rather than a hard "dry".
 *  Fraction of band width, floored so tight bands still get a usable margin. */
function _nearDryMargin(band) {
  const width = Math.max(0, band.max - band.min);
  return Math.max(2, width * 0.25);
}

function _moistBandStatus(moist, band) {
  if (moist == null || !band) return 'unknown';
  if (moist > band.max) return 'wet';
  if (moist >= band.min) return 'ok';
  if (moist >= band.min - _nearDryMargin(band)) return 'near-dry';
  return 'dry';
}

function _moistBandColor(status) {
  switch (status) {
    case 'dry':      return 'var(--warn)';
    case 'near-dry': return 'var(--warn)';
    case 'wet':      return 'var(--wet)';
    case 'ok':       return 'var(--fill-healthy)';
    default:         return 'var(--text-faint)';
  }
}

/** Renders one row per bed: a track spanning 0-100%, the bed's healthy band
 *  shaded within it, and a marker at the live reading. Safe to call every
 *  refresh tick (same pattern as renderBedChips) — pure snapshot, doesn't
 *  depend on the 1h-7d Trends range selector or LAST_INSIGHTS at all. */
function renderMoistureBandCard() {
  const el = document.getElementById('moistband-forecast');
  if (!el) return;

  const rows = BEDS.map(function (bed) {
    const moistKey = bed.sensors.soil_moisture;
    const moist = LATEST_READINGS[moistKey] != null ? parseFloat(LATEST_READINGS[moistKey]) : null;
    const band  = BANDS && BANDS.moistureBands ? BANDS.moistureBands[moistKey] : null;
    const status = _moistBandStatus(moist, band);
    const color  = _moistBandColor(status);

    const margin = band ? _nearDryMargin(band) : 0;
    const bufferHTML = band
      ? '<span class="moistband-buffer" style="left:' + Math.max(0, band.min - margin) +
        '%; width:' + Math.min(band.min, margin) + '%"></span>'
      : '';
    const zoneHTML = band
      ? '<span class="moistband-zone" style="left:' + band.min + '%; width:' + Math.max(0, band.max - band.min) + '%"></span>'
      : '';
    const markerHTML = moist != null
      ? '<span class="moistband-marker" style="left:' + Math.max(0, Math.min(100, moist)) + '%; background:' + color + '"></span>'
      : '';

    const word = status === 'dry' ? 'Dry'
      : status === 'near-dry' ? 'Drying'
      : status === 'wet' ? 'Wet' : 'OK';
    const label = moist == null
      ? 'no reading'
      : word + (band ? ' (' + Math.round(band.min) + '–' + Math.round(band.max) + '%)' : '');

    return (
      '<div class="moistband-row">' +
        '<span class="moistband-bed-name">' + bed.name + '</span>' +
        '<span class="moistband-track" aria-hidden="true">' + bufferHTML + zoneHTML + markerHTML + '</span>' +
        '<span class="moistband-value" style="color:' + color + '">' +
          (moist != null ? Math.round(moist) + '%' : '—') +
        '</span>' +
        '<span class="moistband-label" style="color:' + color + '">' + label + '</span>' +
      '</div>'
    );
  }).join('');

  el.innerHTML = rows;  /* title lives in the outer chart-card wrapper, not here */
}

/* ════════════════════════════════════════════════════════════════════════════
   BED DETAIL (inline expand) — redesign.md §4.1
   Shown directly beneath the bed chip row instead of a side drawer. The old
   drawer's Climate tab was dropped entirely: that ground is already covered
   by the Trends section below, so it was pure duplication.
   ════════════════════════════════════════════════════════════════════════════ */

let OPEN_BED         = null;  /* bed id currently expanded inline, or null */
let LAST_INSIGHTS    = null;  /* most recent /api/insights payload, for live re-render while open */
const bedChartInstances = {}; /* per-bed mini moisture chart, keyed by bed id — updated in place */

/** Toggles a bed's inline detail panel open/closed. Only one bed open at a time. */
function toggleBedDetail(bedId) {
  OPEN_BED = (OPEN_BED === bedId) ? null : bedId;
  if (LAST_INSIGHTS) renderBedChips(LAST_INSIGHTS.beds);
  renderBedDetail();
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && OPEN_BED) {
    OPEN_BED = null;
    if (LAST_INSIGHTS) renderBedChips(LAST_INSIGHTS.beds);
    renderBedDetail();
  }
});

/** Plain-language moisture line + verdict for one bed, from its bed_stress result. */
function _bedVerdict(status, waterBalanceIn) {
  const wb = waterBalanceIn;
  switch (status) {
    case 'dry':  return (wb != null && wb < -0.05) ? 'Water soon, deficit forecast' : 'Water soon';
    case 'wet':  return (wb != null && wb > 0.05)  ? 'Skip irrigation, surplus water balance' : 'Skip irrigation, soil already wet';
    case 'cold': return 'Cold stress, no watering action needed';
    case 'heat': return 'Heat stress, keep moisture up';
    case 'ok':   return 'No action needed';
    default:     return 'Awaiting sensor data';
  }
}

/** The expanded bed's detail: plain-language line, moisture value/band/rate, a
 *  small moisture chart with the healthy band shaded, a quiet battery
 *  indicator, and a single derived verdict line. redesign.md §4.1. */
function _buildBedDetailHTML(bed, stress, waterBalanceIn) {
  const moistKey = bed.sensors.soil_moisture;
  const battKey  = bed.sensors.soil_battery;
  const moistTs  = LATEST_TS[moistKey];
  const stale    = isStale(moistTs);
  const moistVal = LATEST_READINGS[moistKey] != null ? parseFloat(LATEST_READINGS[moistKey]) : null;
  const battVal  = LATEST_READINGS[battKey]  != null ? parseFloat(LATEST_READINGS[battKey])  : null;
  const band     = BANDS && BANDS.moistureBands ? BANDS.moistureBands[moistKey] : null;

  const plainLine = stale
    ? 'No recent reading' + (moistTs ? ' — last seen ' + _minutesAgo(moistTs) + ' min ago.' : '.')
    : (stress ? stress.detail + '.' : 'Awaiting sensor data.');

  const rate = moistVal != null ? _hourlyDelta(moistKey, moistVal, moistTs) : null;
  const rateHTML = rate != null
    ? '<span class="bed-detail-rate ' + (rate > 0.05 ? 'is-up' : rate < -0.05 ? 'is-down' : 'is-flat') + '">' +
        (rate > 0.05 ? '▲' : rate < -0.05 ? '▼' : '■') + ' ' + Math.abs(rate).toFixed(1) + '%/hr' +
      '</span>'
    : '';

  const bandHTML = band ? Math.round(band.min) + '–' + Math.round(band.max) + '% healthy' : '';

  const bedStats = LAST_INSIGHTS && LAST_INSIGHTS.stats ? LAST_INSIGHTS.stats[moistKey] : null;
  const rangeHTML = bedStats
    ? '<span class="bed-detail-metric-range">24h ' + Math.round(bedStats.min) + '–' + Math.round(bedStats.max) + '%</span>'
    : '';

  const battHTML = battVal != null ? buildBatteryBadge(battVal) : '<span class="bed-detail-metric-band">no reading</span>';

  const verdict = stale ? 'Check the sensor, no recent data' : _bedVerdict(stress ? stress.status : 'unknown', waterBalanceIn);

  return (
    '<div class="bed-detail-inner">' +
      '<div class="bed-detail-line">' + plainLine + '</div>' +
      '<div class="bed-detail-metric">' +
        '<span class="bed-detail-metric-val" title="Live sensor reading">' + (moistVal != null ? moistVal.toFixed(0) + '%' : '—') + '</span>' +
        '<span class="bed-detail-metric-band">' + bandHTML + '</span>' +
        rangeHTML +
        rateHTML +
      '</div>' +
      '<div class="bed-detail-chart"><canvas id="bed-detail-chart-' + bed.id + '"></canvas></div>' +
      '<div class="bed-detail-foot">' + battHTML + '</div>' +
      '<div class="bed-detail-verdict">' + verdict + '</div>' +
    '</div>'
  );
}

/** Draws/updates the small per-bed moisture chart, reusing whatever the shared
 *  refresh cycle already cached in seriesCache for that bed's sensor key. */
function _drawBedChart(bed) {
  const key = bed.sensors.soil_moisture;
  const canvas = document.getElementById('bed-detail-chart-' + bed.id);
  const rows = seriesCache[key];
  if (!canvas || !rows) return;

  const labels = rows.map(function (r) { return fmtTime(r.ts); });
  const data   = rows.map(function (r) { return r.value; });
  const band   = BANDS && BANDS.moistureBands ? BANDS.moistureBands[key] : null;
  const bands  = band ? [{ yMin: band.min, yMax: band.max, color: 'rgba(63,156,90,0.14)' }] : [];

  let chart = bedChartInstances[bed.id];
  if (!chart) {
    chart = new Chart(canvas, makeChartOpts(cfg_moisture_color(bed)));
    bedChartInstances[bed.id] = chart;
  }
  chart.data.labels           = labels;
  chart.data.datasets[0].data = data;
  chart._bands                = bands;
  chart._lines                = [];
  chart._wateringEvents       = [];
  chart._projection           = null;
  chart.update('none');
}

function cfg_moisture_color(bed) {
  const entry = MOISTURE_GROUP.find(function (m) { return m.bed === bed.name; });
  if (entry) return entry.color;
  return getComputedStyle(document.documentElement).getPropertyValue('--m-moisture').trim() || '#3f9c5a';
}

/** Renders the currently-open bed's detail directly beneath the chip row, or
 *  hides the panel when no bed is expanded. Cheap enough to call on every
 *  insights poll so content stays live while a bed is open. */
function renderBedDetail() {
  const panel = document.getElementById('bed-detail');
  if (!panel) return;

  if (!OPEN_BED) {
    panel.hidden = true;
    panel.innerHTML = '';
    return;
  }

  const bed = BEDS.find(function (b) { return b.id === OPEN_BED; });
  if (!bed) return;

  const byId = {};
  (LAST_INSIGHTS && LAST_INSIGHTS.beds || []).forEach(function (b) { byId[b.id] = b; });
  const wb = LAST_INSIGHTS && LAST_INSIGHTS.forecast ? LAST_INSIGHTS.forecast.water_balance_in : null;

  panel.hidden = false;
  panel.innerHTML = _buildBedDetailHTML(bed, byId[bed.id], wb);
  _drawBedChart(bed);
}

/** Chart color + label for a charted sensor key, from the server-injected CHARTS list. */
function _chartMeta(key) {
  for (var i = 0; i < CHARTS.length; i++) if (CHARTS[i].key === key) return CHARTS[i];
  return null;
}

/* ════════════════════════════════════════════════════════════════════════════
   INSIGHT PANEL
   ════════════════════════════════════════════════════════════════════════════ */

async function loadInsights() {
  var resp;
  try { resp = await fetch('/api/insights'); } catch (_) { return; }
  if (!resp.ok) return;
  var data = await resp.json();

  /* Refresh sky animation data from the forecast — self-heals across midnight */
  if (data.forecast && data.forecast.sunrise_ts && data.forecast.sunset_ts) {
    SKY = {
      sunrise_ts:          data.forecast.sunrise_ts,
      sunset_ts:           data.forecast.sunset_ts,
      sunrise_ts_tomorrow: data.forecast.sunrise_ts_tomorrow || null,
    };
  }
  if (data.forecast && data.forecast.wind_max_mph != null) {
    WIND_MAX_MPH = data.forecast.wind_max_mph;
  }

  /* Live "right now" conditions — drives the sky-strip chip, cloud cover, and rain.
     A failed upstream fetch omits data.current entirely (see api_insights()); keep
     showing the last known-good conditions instead of blanking the chip to "--". */
  CURRENT = data.current || CURRENT;
  updateWeatherRain(CURRENT);

  LAST_INSIGHTS = data;
  renderClimateStrip(data);
  /* Bed chips read LATEST_READINGS/LATEST_TS (for staleness), which refresh()
     only updates after this fetch resolves — render there instead, once both
     are in sync, or every cycle would flash "Offline" until the next click. */
  if (LATEST_UPDATED_TS) {
    renderBedChips(data.beds);
    if (OPEN_BED) renderBedDetail();
  }
}

/** Single fetch feeds both the stat strip and the garden */
async function refresh() {
  const dot = document.getElementById('conn-dot');
  if (dot) dot.classList.add('is-fetching');

  /* Fetch /api/latest, all chart series, and /api/insights in parallel so
     seriesCache is populated before updateStats() renders sparklines, and
     CURRENT (insights) is fresh before updateGarden() paints the "Now" chip
     off of it -- previously insights was kicked off *after* updateGarden,
     so the chip always painted from the prior cycle's conditions. */
  var chartLoads = CHARTS.map(function (c) { return loadChart(c.key, c.color); });
  chartLoads = chartLoads.concat(MOISTURE_GROUP.map(function (m) { return loadChart(m.key, m.color); }));
  var insightsLoad = loadInsights();
  var results       = await Promise.all([fetch('/api/latest')].concat(chartLoads));
  var latestResp    = results[0];
  await insightsLoad;

  if (latestResp.ok) {
    const rows = await latestResp.json();
    const parsed = parseLatest(rows);
    LATEST_READINGS   = parsed.readings;
    LATEST_TS         = parsed.ts;
    LATEST_UPDATED_TS = parsed.lastUpdated;
    updateStats(rows);    /* seriesCache is now populated — sparklines render */
    updateGarden(rows);
    if (LAST_INSIGHTS) {
      /* loadInsights() may have rendered the climate strip before this fetch
         set LATEST_UPDATED_TS (both run in parallel), leaving a one-cycle-stale
         staleness read -- e.g. "data offline" flashing on first load until the
         next refresh. Re-render now that the timestamp is actually current. */
      renderClimateStrip(LAST_INSIGHTS);
      renderBedChips(LAST_INSIGHTS.beds);
      if (OPEN_BED) renderBedDetail();
    }
  } else {
    _updateConnDot(null);
  }

  renderTrendsClimateGrid();  /* redraw with the freshly loaded seriesCache */

  if (dot) dot.classList.remove('is-fetching');
}

/* ════════════════════════════════════════════════════════════════════════════
   EASTER EGG — tap a vegetable 4× rapidly to make it explode + rain 🎉
   ════════════════════════════════════════════════════════════════════════════ */

const EGG_TAP_LIMIT  = 4;      /* taps required (within EGG_TAP_WINDOW) to trigger */
const EGG_TAP_WINDOW = 1500;   /* ms — pause this long and the streak resets */
const eggTapState = new WeakMap();  /* plantEl -> {count, timer} */

let eggLayer = null;
function getEggLayer() {
  if (!eggLayer) {
    eggLayer = document.createElement('div');
    eggLayer.className = 'g-egg-layer';
    document.body.appendChild(eggLayer);
  }
  return eggLayer;
}

function spawnEggParticle(cls, style) {
  const el = document.createElement('span');
  el.className = 'g-egg ' + cls;
  Object.keys(style).forEach(function (k) { el.style.setProperty(k, style[k]); });
  el.addEventListener('animationend', function () { el.remove(); });
  getEggLayer().appendChild(el);
  return el;
}

function explodeEmoji(emoji, rect) {
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const layer = getEggLayer();

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) {
    /* minimal, non-flashy fallback: a handful of emoji fade in place near the plant */
    for (let i = 0; i < 6; i++) {
      const a = Math.random() * Math.PI * 2;
      const d = 18 + Math.random() * 30;
      const el = spawnEggParticle('g-egg-still', {
        '--x': (cx + Math.cos(a) * d) + 'px',
        '--y': (cy + Math.sin(a) * d) + 'px',
      });
      el.textContent = emoji;
    }
    return;
  }

  /* ── burst: radial spray from the tapped plant ── */
  const burstCount = 56;
  for (let i = 0; i < burstCount; i++) {
    const angle    = Math.random() * Math.PI * 2;
    const distance = 90 + Math.random() * 260;
    const el = spawnEggParticle('g-egg-burst', {
      '--x':     cx + 'px',
      '--y':     cy + 'px',
      '--tx':    (Math.cos(angle) * distance) + 'px',
      '--ty':    (Math.sin(angle) * distance) + 'px',
      '--rot':   ((Math.random() - 0.5) * 720) + 'deg',
      '--scale': (0.7 + Math.random() * 1.1).toFixed(2),
      '--delay': (Math.random() * 0.12) + 's',
    });
    el.textContent = emoji;
  }

  /* ── rain: a brief shower across the whole viewport ── */
  const vw = window.innerWidth;
  const rainCount = 34;
  for (let i = 0; i < rainCount; i++) {
    const x = Math.random() * vw;
    const el = spawnEggParticle('g-egg-rain', {
      '--x':     x + 'px',
      '--drift': ((Math.random() - 0.5) * 120) + 'px',
      '--rot':   ((Math.random() - 0.5) * 540) + 'deg',
      '--dur':   (1.4 + Math.random() * 0.9) + 's',
      '--delay': (0.1 + Math.random() * 0.5) + 's',
    });
    el.textContent = emoji;
  }
}

/* ── Live weather rain/snow — driven by CURRENT.is_raining / .intensity /
   .weather_code ── Light rain: a small looping shower clipped to the
   full-page sky backdrop's .sky-band. Heavy rain / thunderstorms: escalates
   to a full-page downpour reusing the easter-egg particle system
   (spawnEggParticle / g-egg-rain). Snow WMO codes get looping flurries
   instead of rain. Respects prefers-reduced-motion the same way the easter
   egg does. */
let _heavyRainTimer = null;

function updateWeatherRain(current) {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const band = document.getElementById('sky-band');
  const isSnow  = !!(current && _SNOW_CODES.has(current.weather_code));
  const raining = !reduced && !isSnow && !!(current && current.is_raining);
  const snowing = !reduced && isSnow;

  if (band) {
    const hasDrops = !!band.querySelector('.sky-rain-drop');
    if (raining && !hasDrops) {
      _addSkyRainDrops(band);
    } else if (!raining && hasDrops) {
      band.querySelectorAll('.sky-rain-drop').forEach(function (d) { d.remove(); });
    }

    const hasFlakes = !!band.querySelector('.sky-snowflake');
    if (snowing && !hasFlakes) {
      _addSkySnowflakes(band);
    } else if (!snowing && hasFlakes) {
      band.querySelectorAll('.sky-snowflake').forEach(function (f) { f.remove(); });
    }
  }

  const heavy = raining && current.intensity === 'heavy';
  if (heavy) _startHeavyRain(); else _stopHeavyRain();
}

function _addSkyRainDrops(band) {
  const dropCount = 14;
  const fall  = (band.clientHeight || 420) + 30;
  const wind  = WIND_MAX_MPH || 0;
  const drift = Math.min(60, wind * 1.5);  /* wind slants the fall, capped */
  for (let i = 0; i < dropCount; i++) {
    const el = document.createElement('span');
    el.className = 'sky-rain-drop';
    el.setAttribute('aria-hidden', 'true');
    el.style.left = (Math.random() * 96) + '%';
    el.style.setProperty('--rdur',   (0.7 + Math.random() * 0.5) + 's');
    el.style.setProperty('--rdelay', (Math.random() * 1.2) + 's');
    el.style.setProperty('--rfall',  fall + 'px');
    el.style.setProperty('--rdrift', (drift * (0.6 + Math.random() * 0.4)) + 'px');
    band.appendChild(el);
  }
}

function _addSkySnowflakes(band) {
  const flakes = ['❄', '❅', '❆'];
  const count  = 18;
  const fall   = (band.clientHeight || 420) + 30;
  for (let i = 0; i < count; i++) {
    const el = document.createElement('span');
    el.className = 'sky-snowflake';
    el.setAttribute('aria-hidden', 'true');
    el.textContent = flakes[i % flakes.length];
    el.style.left = (Math.random() * 96) + '%';
    el.style.setProperty('--sdur',   (6 + Math.random() * 5) + 's');
    el.style.setProperty('--sdelay', (Math.random() * 4) + 's');
    el.style.setProperty('--sfall',  fall + 'px');
    el.style.setProperty('--sdrift', ((Math.random() - 0.3) * 40) + 'px');
    el.style.setProperty('--sfsize', (8 + Math.random() * 6) + 'px');
    band.appendChild(el);
  }
}

function _startHeavyRain() {
  if (_heavyRainTimer) return;   /* already running */
  _heavyRainTimer = setInterval(function () {
    const vw = window.innerWidth;
    for (let i = 0; i < 6; i++) {
      const x = Math.random() * vw;
      const el = spawnEggParticle('g-egg-rain weather-rain-drop', {
        '--x':     x + 'px',
        '--drift': ((Math.random() - 0.5) * 80) + 'px',
        '--rot':   '0deg',
        '--dur':   (1.0 + Math.random() * 0.6) + 's',
        '--delay': (Math.random() * 0.3) + 's',
      });
      el.textContent = '💧';
    }
  }, 350);
}

function _stopHeavyRain() {
  if (_heavyRainTimer) {
    clearInterval(_heavyRainTimer);
    _heavyRainTimer = null;
  }
}

document.getElementById('garden-beds').addEventListener('click', function (e) {
  const plant = e.target.closest('.g-plant');
  if (!plant) return;

  const prev = eggTapState.get(plant);
  if (prev && prev.timer) clearTimeout(prev.timer);
  const count = (prev ? prev.count : 0) + 1;

  if (count >= EGG_TAP_LIMIT) {
    const type  = plant.dataset.type;
    const emoji = PLANT_EMOJI[type] || PLANT_EMOJI.unknown;
    explodeEmoji(emoji, plant.getBoundingClientRect());
    eggTapState.delete(plant);   /* re-arm immediately for another round */
    /* Let the burst read as the game's "launch" flourish, then cut in --
       see GARDEN NINJA below. Guarded there against a game already running.
       Unlock audio here (synchronously, inside the click gesture) rather
       than inside the delayed startNinjaGame() -- Safari's autoplay policy
       won't grant an AudioContext created from a setTimeout callback. */
    ninjaAudio.unlock();
    setTimeout(startNinjaGame, 550);
    return;
  }

  const timer = setTimeout(function () { eggTapState.delete(plant); }, EGG_TAP_WINDOW);
  eggTapState.set(plant, { count: count, timer: timer });
});

/* ════════════════════════════════════════════════════════════════════════════
   GARDEN NINJA — fruit-slicing mini-game, launched from the tap-4x easter egg.
   Single-file, no build step, no external assets: canvas game field + a small
   WebAudio synth for sound effects. Lazily built on first launch and reused
   on subsequent launches (mirrors getEggLayer()'s lazy-singleton pattern).
   ════════════════════════════════════════════════════════════════════════════ */

/** Slice-able produce pool (weighted plain), the golden bonus wrapper, and the
 *  one hazard. Reuses PLANT_EMOJI's veg where it makes sense, plus a few extra
 *  garden fruits for visual variety -- purely decorative, no gameplay meaning
 *  beyond "bonus" scoring more. */
const NINJA_FRUIT   = ['🍅', '🍆', '🌶️', '🫑', '🥒', '🫛', '🍓', '🫐', '🌽', '🎃', '🥕'];
const NINJA_BOMB     = '💣';

const NINJA_MISS_LIMIT   = 3;     /* uncut fruit allowed before game over */
const NINJA_MAX_ALIVE    = 10;    /* hard cap on simultaneous airborne entities */
const NINJA_RAMP_MS      = 60000; /* time to reach fastest spawn rate + full speed */
const NINJA_SPAWN_START  = 1400;  /* ms between spawns at t=0 -- deliberately gentle */
const NINJA_SPAWN_END    = 350;   /* ms between spawns once fully ramped */
const NINJA_GRAVITY      = 1500;  /* px/s^2, at full speed (t=1) */
/* Motion "slow-motion" factor applied to both velocity and gravity together,
   so flight *duration* stays reasonable even at low speed -- only how high/
   fast things move scales down. Ramps 0.5 -> 1.05 over NINJA_RAMP_MS. */
const NINJA_SPEED_START  = 0.5;
const NINJA_SPEED_END    = 1.05;

const ninja = {
  active: false,
  paused: false,
  layer: null, canvas: null, ctx: null, hud: null,
  scoreEl: null, livesEl: null, hintEl: null, gameoverEl: null,
  dpr: 1, w: 0, h: 0,
  entities: [],   /* airborne fruit/bombs */
  juice: [],      /* splatter particles */
  popups: [],     /* floating +score text */
  trail: [],      /* recent pointer points for the slice trail */
  score: 0,
  lives: NINJA_MISS_LIMIT,
  startedAt: 0,
  lastTs: 0,
  spawnAt: 0,
  rafId: null,
  reduced: false,
};

function _ninjaBest() {
  return parseInt(localStorage.getItem('gardenNinjaBest') || '0', 10) || 0;
}

/* ── tiny WebAudio synth: no asset files, created lazily on first user gesture ── */
const ninjaAudio = {
  ctx: null,
  muted: localStorage.getItem('gardenNinjaMuted') === '1',
  unlock: function () {
    /* Must run synchronously inside a user-gesture handler (click/tap) --
       Safari and other strict autoplay policies won't grant audio to a
       context created from inside a setTimeout/async callback. */
    if (!this.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this.ctx = new Ctx();
    }
    if (this.ctx.state === 'suspended') this.ctx.resume();
  },
  toggleMute: function () {
    this.muted = !this.muted;
    localStorage.setItem('gardenNinjaMuted', this.muted ? '1' : '0');
    return this.muted;
  },
  _tone: function (freq, dur, type, gain) {
    if (this.muted || !this.ctx) return;
    const t0  = this.ctx.currentTime;
    const osc = this.ctx.createOscillator();
    const g   = this.ctx.createGain();
    osc.type = type || 'sine';
    osc.frequency.setValueAtTime(freq, t0);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain || 0.16, t0 + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(this.ctx.destination);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  },
  swoosh: function () {
    if (this.muted || !this.ctx) return;
    const t0 = this.ctx.currentTime;
    const bufSize = Math.floor(this.ctx.sampleRate * 0.18);
    const buf  = this.ctx.createBuffer(1, bufSize, this.ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < bufSize; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / bufSize);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    const filt = this.ctx.createBiquadFilter();
    filt.type = 'bandpass';
    filt.frequency.setValueAtTime(1800, t0);
    filt.frequency.exponentialRampToValueAtTime(500, t0 + 0.18);
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(0.14, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.18);
    src.connect(filt).connect(g).connect(this.ctx.destination);
    src.start(t0);
  },
  pop: function (bonus) { this._tone(bonus ? 920 : 660, 0.12, 'triangle', 0.18); },
  boom: function () {
    if (this.muted || !this.ctx) return;
    const t0 = this.ctx.currentTime;
    const osc = this.ctx.createOscillator();
    const g   = this.ctx.createGain();
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(160, t0);
    osc.frequency.exponentialRampToValueAtTime(30, t0 + 0.5);
    g.gain.setValueAtTime(0.3, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.55);
    osc.connect(g).connect(this.ctx.destination);
    osc.start(t0);
    osc.stop(t0 + 0.6);
  },
  over: function () {
    if (this.muted || !this.ctx) return;
    const notes = [440, 349, 262];
    const self = this;
    notes.forEach(function (f, i) {
      setTimeout(function () { self._tone(f, 0.22, 'square', 0.14); }, i * 110);
    });
  },
};

function _ninjaBuildOverlay() {
  if (ninja.layer) return;

  const layer = document.createElement('div');
  layer.className = 'g-ninja-layer';
  layer.setAttribute('role', 'dialog');
  layer.setAttribute('aria-label', 'Garden Ninja mini-game');

  const canvas = document.createElement('canvas');
  canvas.className = 'g-ninja-canvas';
  layer.appendChild(canvas);

  const hud = document.createElement('div');
  hud.className = 'g-ninja-hud';
  hud.innerHTML =
    '<div class="g-ninja-score"><span class="g-ninja-score-label">Score</span>' +
      '<span class="g-ninja-score-value">0</span></div>' +
    '<div class="g-ninja-lives" aria-hidden="true"></div>' +
    '<div class="g-ninja-controls">' +
      '<button type="button" class="g-ninja-btn g-ninja-mute" aria-label="Toggle sound"></button>' +
      '<button type="button" class="g-ninja-btn g-ninja-close" aria-label="Close game">✕</button>' +
    '</div>';
  layer.appendChild(hud);

  const hint = document.createElement('div');
  hint.className = 'g-ninja-hint';
  hint.textContent = '🔪 Slide to slice! Avoid the 💣';
  layer.appendChild(hint);

  const gameover = document.createElement('div');
  gameover.className = 'g-ninja-gameover';
  gameover.innerHTML =
    '<div class="g-ninja-panel">' +
      '<h2>Game Over</h2>' +
      '<p class="g-ninja-reason"></p>' +
      '<div class="g-ninja-stats">' +
        '<div class="g-ninja-stat"><div class="g-ninja-stat-value g-ninja-final-score">0</div>' +
          '<div class="g-ninja-stat-label">Score</div></div>' +
        '<div class="g-ninja-stat"><div class="g-ninja-stat-value g-ninja-best-score">0</div>' +
          '<div class="g-ninja-stat-label">Best</div></div>' +
      '</div>' +
      '<div class="g-ninja-panel-actions">' +
        '<button type="button" class="g-ninja-close-btn">Close</button>' +
        '<button type="button" class="g-ninja-primary g-ninja-again">Play again</button>' +
      '</div>' +
    '</div>';
  layer.appendChild(gameover);

  document.body.appendChild(layer);

  ninja.layer = layer;
  ninja.canvas = canvas;
  ninja.ctx = canvas.getContext('2d');
  ninja.hud = hud;
  ninja.scoreEl = hud.querySelector('.g-ninja-score-value');
  ninja.livesEl = hud.querySelector('.g-ninja-lives');
  ninja.hintEl = hint;
  ninja.gameoverEl = gameover;

  hud.querySelector('.g-ninja-close').addEventListener('click', function () { _ninjaClose(); });
  hud.querySelector('.g-ninja-mute').addEventListener('click', function () {
    const muted = ninjaAudio.toggleMute();
    this.textContent = muted ? '🔇' : '🔊';
  });
  gameover.querySelector('.g-ninja-close-btn').addEventListener('click', function () { _ninjaClose(); });
  gameover.querySelector('.g-ninja-again').addEventListener('click', function () { startNinjaGame(); });

  canvas.addEventListener('pointerdown', _ninjaPointerDown);
  canvas.addEventListener('pointermove', _ninjaPointerMove);
  canvas.addEventListener('pointerup', _ninjaPointerUp);
  canvas.addEventListener('pointercancel', _ninjaPointerUp);

  window.addEventListener('resize', _ninjaResize);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && ninja.active) _ninjaClose();
  });
  document.addEventListener('visibilitychange', function () {
    if (!ninja.active) return;
    if (document.hidden) _ninjaPause(); else _ninjaResume();
  });
}

function _ninjaResize() {
  if (!ninja.canvas) return;
  ninja.dpr = window.devicePixelRatio || 1;
  ninja.w = window.innerWidth;
  ninja.h = window.innerHeight;
  ninja.canvas.width = Math.floor(ninja.w * ninja.dpr);
  ninja.canvas.height = Math.floor(ninja.h * ninja.dpr);
  ninja.ctx.setTransform(ninja.dpr, 0, 0, ninja.dpr, 0, 0);
}

function _ninjaRenderLives() {
  ninja.livesEl.innerHTML = '';
  for (let i = 0; i < NINJA_MISS_LIMIT; i++) {
    const span = document.createElement('span');
    span.className = 'g-ninja-life' + (i < ninja.lives ? '' : ' is-lost');
    span.textContent = '❤️';
    ninja.livesEl.appendChild(span);
  }
}

function startNinjaGame() {
  if (ninja.active) return;
  _ninjaBuildOverlay();
  ninjaAudio.unlock();

  ninja.active = true;
  ninja.paused = false;
  ninja.entities = [];
  ninja.juice = [];
  ninja.popups = [];
  ninja.trail = [];
  ninja.score = 0;
  ninja.lives = NINJA_MISS_LIMIT;
  ninja.startedAt = performance.now();
  ninja.lastTs = ninja.startedAt;
  ninja.spawnAt = ninja.startedAt + 250;
  ninja.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  ninja.scoreEl.textContent = '0';
  _ninjaRenderLives();
  ninja.gameoverEl.classList.remove('is-visible');
  ninja.hud.querySelector('.g-ninja-mute').textContent = ninjaAudio.muted ? '🔇' : '🔊';

  _ninjaResize();
  ninja.layer.classList.add('is-open');
  document.body.style.overflow = 'hidden';

  ninja.hintEl.classList.remove('is-showing');
  void ninja.hintEl.offsetWidth; /* restart the hint animation each round */
  ninja.hintEl.classList.add('is-showing');

  if (ninja.rafId) cancelAnimationFrame(ninja.rafId);
  ninja.rafId = requestAnimationFrame(_ninjaTick);
}

function _ninjaPause() {
  ninja.paused = true;
  if (ninja.rafId) { cancelAnimationFrame(ninja.rafId); ninja.rafId = null; }
}
function _ninjaResume() {
  if (!ninja.active || !ninja.paused) return;
  ninja.paused = false;
  ninja.lastTs = performance.now();
  ninja.spawnAt = ninja.lastTs + 300;
  ninja.rafId = requestAnimationFrame(_ninjaTick);
}

function _ninjaClose() {
  ninja.active = false;
  ninja.paused = false;
  if (ninja.rafId) { cancelAnimationFrame(ninja.rafId); ninja.rafId = null; }
  if (ninja.layer) ninja.layer.classList.remove('is-open');
  document.body.style.overflow = '';
}

function _ninjaGameOver(reason) {
  if (!ninja.active) return;
  ninja.active = false;
  if (ninja.rafId) { cancelAnimationFrame(ninja.rafId); ninja.rafId = null; }
  ninjaAudio.over();

  const best = Math.max(_ninjaBest(), ninja.score);
  localStorage.setItem('gardenNinjaBest', String(best));

  ninja.gameoverEl.querySelector('.g-ninja-reason').textContent =
    reason === 'bomb' ? 'Boom! You sliced a bomb.' : 'Out of lives -- too many slipped by.';
  ninja.gameoverEl.querySelector('.g-ninja-final-score').textContent = String(ninja.score);
  ninja.gameoverEl.querySelector('.g-ninja-best-score').textContent = String(best);
  ninja.gameoverEl.classList.add('is-visible');
}

/** Fruit size scales off the viewport's smaller dimension so touch targets
 *  stay comfortable on phones and fruit isn't lost as a speck on wide desktop
 *  screens. Clamped to a sane range either way. */
function _ninjaFruitRadius() {
  const minDim = Math.min(ninja.w, ninja.h);
  return Math.max(30, Math.min(48, minDim * 0.06));
}

/** 0..1 progress through the difficulty ramp window. */
function _ninjaRampT(elapsed) {
  return Math.min(1, elapsed / NINJA_RAMP_MS);
}

/** Slow-motion factor applied to both velocity and gravity together, so
 *  flight *duration* stays reasonable even when things start slow -- only
 *  how high/fast fruit moves scales down, not how long it hangs in the air. */
function _ninjaSpeedFactor(elapsed) {
  const t = _ninjaRampT(elapsed);
  return NINJA_SPEED_START + (NINJA_SPEED_END - NINJA_SPEED_START) * t;
}

/* ── entity spawning ── */
function _ninjaSpawn(elapsed) {
  if (ninja.entities.length >= NINJA_MAX_ALIVE) return;

  const fromLeft = Math.random() < 0.5;
  const roll = Math.random();
  let kind = 'fruit', emoji, bonus = false;
  if (roll < 0.10) {
    kind = 'bomb'; emoji = NINJA_BOMB;
  } else if (roll < 0.20) {
    bonus = true; emoji = NINJA_FRUIT[Math.floor(Math.random() * NINJA_FRUIT.length)];
  } else {
    emoji = NINJA_FRUIT[Math.floor(Math.random() * NINJA_FRUIT.length)];
  }

  const speed = _ninjaSpeedFactor(elapsed);
  const x = fromLeft ? -20 : ninja.w + 20;
  const y = ninja.h + 30;
  const targetX = ninja.w * (0.28 + Math.random() * 0.44); /* aim toward center-ish */
  /* Peak (apex of the arc) always lands at or above screen middle (0.5h),
     with some flying higher for variety -- guarantees every toss actually
     reaches the middle instead of falling short. Vertical physics use the
     constant NINJA_GRAVITY always (not scaled by `speed`) so this guarantee
     holds even at game start; only horizontal drift ramps up over time. */
  const peakHeight = ninja.h * (0.30 + Math.random() * 0.20);
  const vx = ((targetX - x) / 1.1) * speed;
  const vy = -Math.sqrt(2 * NINJA_GRAVITY * (ninja.h - peakHeight));

  ninja.entities.push({
    emoji: emoji, kind: kind, bonus: bonus,
    x: x, y: y, vx: vx, vy: vy,
    rot: Math.random() * Math.PI * 2,
    vr: (Math.random() - 0.5) * 4,
    radius: _ninjaFruitRadius(),
    sliced: false,
  });
}

function _ninjaSpawnInterval(elapsed) {
  const t = _ninjaRampT(elapsed);
  return NINJA_SPAWN_START + (NINJA_SPAWN_END - NINJA_SPAWN_START) * t;
}

/* ── juice / popup particles ── */
const NINJA_JUICE_COLOR = {
  '🍅': '#e0503f', '🍆': '#5a3f8a', '🌶️': '#c0503f', '🫑': '#4a9c5a',
  '🥒': '#6fa64c', '🫛': '#7ab04a', '🍓': '#e0475f', '🫐': '#4a4a9c',
  '🌽': '#e0b83c', '🎃': '#e08a2b', '🥕': '#e08a2b',
};
function _ninjaJuiceBurst(x, y, emoji) {
  const color = NINJA_JUICE_COLOR[emoji] || '#9c7350';
  const count = ninja.reduced ? 4 : 12;
  for (let i = 0; i < count; i++) {
    const a = Math.random() * Math.PI * 2;
    const speed = 80 + Math.random() * 220;
    ninja.juice.push({
      x: x, y: y,
      vx: Math.cos(a) * speed, vy: Math.sin(a) * speed - 60,
      life: 0, maxLife: 0.45 + Math.random() * 0.25,
      r: 2 + Math.random() * 3.5, color: color,
    });
  }
}
/** Canvas fillStyle can't resolve CSS custom properties (var(--accent)
 *  is silently ignored, not an error) -- read the theme's actual computed
 *  color instead so popups stay on-brand across light/dark. */
function _ninjaAccentColor() {
  return getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#57b874';
}
function _ninjaPopup(x, y, text, color) {
  ninja.popups.push({ x: x, y: y, text: text, life: 0, maxLife: 0.7, color: color || _ninjaAccentColor() });
}

/* ── slicing ── */
function _ninjaPointerDown(e) {
  if (!ninja.active) return;
  ninja.trail = [{ x: e.clientX, y: e.clientY, t: performance.now() }];
}
function _ninjaPointerUp() {
  ninja.trail = [];
}
function _ninjaPointerMove(e) {
  if (!ninja.active || ninja.paused) return;
  const now = performance.now();
  const pt = { x: e.clientX, y: e.clientY, t: now };
  const prev = ninja.trail.length ? ninja.trail[ninja.trail.length - 1] : null;
  ninja.trail.push(pt);
  while (ninja.trail.length > 14) ninja.trail.shift();

  if (!prev) return;
  const dist = Math.hypot(pt.x - prev.x, pt.y - prev.y);
  if (dist < 4) return; /* ignore jitter/hover noise */

  for (let i = 0; i < ninja.entities.length; i++) {
    const ent = ninja.entities[i];
    if (ent.sliced) continue;
    if (_ninjaSegmentHitsCircle(prev.x, prev.y, pt.x, pt.y, ent.x, ent.y, ent.radius)) {
      _ninjaSlice(ent);
    }
  }
}
function _ninjaSegmentHitsCircle(x1, y1, x2, y2, cx, cy, r) {
  const dx = x2 - x1, dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  let t = len2 === 0 ? 0 : ((cx - x1) * dx + (cy - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const px = x1 + t * dx, py = y1 + t * dy;
  return Math.hypot(cx - px, cy - py) <= r;
}

function _ninjaSlice(ent) {
  ent.sliced = true;
  if (ent.kind === 'bomb') {
    ninjaAudio.boom();
    if (!ninja.reduced) _ninjaShake();
    _ninjaJuiceBurst(ent.x, ent.y, '💣');
    _ninjaGameOver('bomb');
    return;
  }
  const gained = ent.bonus ? 3 : 1;
  ninja.score += gained;
  ninja.scoreEl.textContent = String(ninja.score);
  ninjaAudio.pop(ent.bonus);
  _ninjaJuiceBurst(ent.x, ent.y, ent.emoji);
  _ninjaPopup(ent.x, ent.y, '+' + gained, ent.bonus ? '#e0a44a' : null);
}

function _ninjaShake() {
  ninja.layer.classList.remove('is-shaking');
  void ninja.layer.offsetWidth;
  ninja.layer.classList.add('is-shaking');
}

function _ninjaMiss(ent) {
  ninja.lives--;
  _ninjaRenderLives();
  if (!ninja.reduced) _ninjaShake();
  if (ninja.lives <= 0) _ninjaGameOver('miss');
}

/* ── main loop ── */
function _ninjaTick(ts) {
  if (!ninja.active || ninja.paused) return;
  const dt = Math.min(0.05, (ts - ninja.lastTs) / 1000); /* clamp to avoid huge steps on tab-back */
  ninja.lastTs = ts;
  const elapsed = ts - ninja.startedAt;

  if (ts >= ninja.spawnAt) {
    _ninjaSpawn(elapsed);
    /* occasional quick volley once the game has ramped up a bit */
    if (elapsed > NINJA_RAMP_MS * 0.65 && Math.random() < 0.22) _ninjaSpawn(elapsed);
    ninja.spawnAt = ts + _ninjaSpawnInterval(elapsed);
  }

  /* physics */
  for (let i = ninja.entities.length - 1; i >= 0; i--) {
    const ent = ninja.entities[i];
    ent.vy += NINJA_GRAVITY * dt;
    ent.x += ent.vx * dt;
    ent.y += ent.vy * dt;
    ent.rot += ent.vr * dt;
    const gone = ent.y > ninja.h + 60 || ent.x < -80 || ent.x > ninja.w + 80;
    if (ent.sliced || gone) {
      if (gone && !ent.sliced && ent.kind !== 'bomb') _ninjaMiss(ent);
      ninja.entities.splice(i, 1);
    }
  }
  for (let i = ninja.juice.length - 1; i >= 0; i--) {
    const p = ninja.juice[i];
    p.life += dt;
    if (p.life >= p.maxLife) { ninja.juice.splice(i, 1); continue; }
    p.vy += NINJA_GRAVITY * 0.5 * dt;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
  }
  for (let i = ninja.popups.length - 1; i >= 0; i--) {
    const p = ninja.popups[i];
    p.life += dt;
    if (p.life >= p.maxLife) { ninja.popups.splice(i, 1); continue; }
    p.y -= 40 * dt;
  }

  _ninjaDraw();
  if (ninja.active) ninja.rafId = requestAnimationFrame(_ninjaTick);
}

function _ninjaDraw() {
  const ctx = ninja.ctx;
  ctx.clearRect(0, 0, ninja.w, ninja.h);

  /* slice trail */
  if (ninja.trail.length > 1) {
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (let i = 1; i < ninja.trail.length; i++) {
      const a = ninja.trail[i - 1], b = ninja.trail[i];
      const alpha = i / ninja.trail.length;
      ctx.strokeStyle = 'rgba(255,255,255,' + (alpha * 0.85).toFixed(2) + ')';
      ctx.lineWidth = 2 + alpha * 4;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.restore();
  }

  /* entities */
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ninja.entities.forEach(function (ent) {
    ctx.save();
    ctx.translate(ent.x, ent.y);
    ctx.rotate(ent.rot);
    if (ent.bonus) {
      ctx.shadowColor = '#f4c542';
      ctx.shadowBlur = 22;
    }
    ctx.font = (ent.radius * 1.6) + 'px serif';
    ctx.fillText(ent.emoji, 0, 0);
    ctx.restore();
  });

  /* juice splatter */
  ninja.juice.forEach(function (p) {
    const alpha = 1 - p.life / p.maxLife;
    ctx.fillStyle = p.color;
    ctx.globalAlpha = Math.max(0, alpha);
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.globalAlpha = 1;

  /* score popups */
  ctx.font = '700 20px sans-serif';
  ninja.popups.forEach(function (p) {
    const alpha = 1 - p.life / p.maxLife;
    ctx.globalAlpha = Math.max(0, alpha);
    ctx.fillStyle = p.color || '#57b874';
    ctx.fillText(p.text, p.x, p.y);
  });
  ctx.globalAlpha = 1;
}

/** Soft skeleton placeholders for the chip row + climate stats while the
 *  first /api/latest + /api/insights round trip is in flight (redesign.md
 *  §9 "Loading"). Rendered synchronously so there's never a blank flash;
 *  renderBedChips()/renderClimateStrip() overwrite these on first real data. */
function renderLoadingSkeletons() {
  const chipRow = document.getElementById('bed-chip-row');
  if (chipRow && !chipRow.children.length) {
    chipRow.innerHTML = BEDS.map(function () {
      return '<div class="bed-chip is-skeleton" aria-hidden="true"></div>';
    }).join('');
  }
  const statsEl = document.getElementById('climate-stats');
  if (statsEl && !statsEl.children.length) {
    statsEl.innerHTML = '<div class="climate-stat is-skeleton" aria-hidden="true"></div>'.repeat(3);
  }
}

/* ── Boot ── */
renderBeds();
renderBedMoistureCards();
renderLoadingSkeletons();
_tickClock();
setInterval(_tickClock, 15_000);
refresh();
setInterval(refresh, 60_000);
