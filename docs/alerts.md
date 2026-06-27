# Alert Rules Reference

All rules are deterministic Python checks in `garden/agent/rules.py`. The LLM (`claude-haiku-4-5`) writes the prose for each fired alert — it never decides whether to alert.

## Rules

### `soil_moisture_low`

Fires when a soil moisture sensor reads below the threshold for N consecutive readings.

| Config key | Default | Description |
|------------|---------|-------------|
| `thresholds.soil_moisture_low.below` | `30` | Moisture % threshold |
| `thresholds.soil_moisture_low.consecutive` | `3` | Consecutive readings required |
| `cooldowns.soil_moisture_low_minutes` | `120` | Re-alert suppression window |

Sensors: `soilmoisture1`, `soilmoisture2`
Cadence: instant (fires on POST)

---

### `soil_moisture_rapid_drop`

Fires when moisture drops more than N percentage points within a rolling window.

| Config key | Default | Description |
|------------|---------|-------------|
| `thresholds.soil_moisture_rapid_drop.drop_pct` | `15` | Drop in % points to trigger |
| `thresholds.soil_moisture_rapid_drop.window_minutes` | `60` | Rolling window |
| `cooldowns.soil_moisture_rapid_drop_minutes` | `60` | Re-alert suppression window |

Sensors: `soilmoisture1`, `soilmoisture2`
Cadence: instant (fires on POST)

---

### `battery_low`

Fires when a soil sensor battery drops below threshold.

| Config key | Default | Description |
|------------|---------|-------------|
| `thresholds.battery_low.below` | `1.1` | Voltage threshold |
| `cooldowns.battery_low_minutes` | `1440` | Once per day |

Sensors: `soilbatt1`, `soilbatt2`
Cadence: instant (fires on POST)

---

### `temp_frost`

Fires when outdoor temperature drops below frost warning threshold.

| Config key | Default | Description |
|------------|---------|-------------|
| `thresholds.temp_frost.below` | `35.6` | °F threshold (warns before 32°F) |
| `cooldowns.temp_frost_minutes` | `60` | Re-alert suppression window |

Sensors: `temp_f`
Cadence: instant (fires on POST)

---

### `temp_heat`

Fires when outdoor temperature exceeds heat stress threshold.

| Config key | Default | Description |
|------------|---------|-------------|
| `thresholds.temp_heat.above` | `100.4` | °F threshold |
| `cooldowns.temp_heat_minutes` | `60` | Re-alert suppression window |

Sensors: `temp_f`
Cadence: instant (fires on POST)

---

### `watchdog` (per sensor)

Fires when a known sensor stops reporting for longer than `sensor_timeout_minutes`.

| Config key | Default | Description |
|------------|---------|-------------|
| `watchdog.sensor_timeout_minutes` | `30` | Silence window before alert |
| `cooldowns.watchdog_minutes` | `30` | Re-alert suppression window |

Only fires for sensors that have been seen at least once (never alerts on sensors not yet wired).
Cadence: cron (every 15 min)

---

### `watchdog:gateway`

Fires when no POST at all has arrived from the GW1200 gateway.

| Config key | Default | Description |
|------------|---------|-------------|
| `watchdog.timeout_minutes` | `20` | Gateway silence window |
| `cooldowns.watchdog_minutes` | `30` | Re-alert suppression window |

Cadence: cron (every 15 min)

---

## Cooldown + alert-once-until-cleared

- When a rule fires, it is marked `active` in `alert_state`.
- While `active`, re-alerts are suppressed for the cooldown window.
- Once the condition clears (e.g. moisture recovers), `active` is reset — the rule re-arms and will fire again next time the condition is tripped.

## Morning Brief

Replaces the old heartbeat. Sent once per day at `daily_brief.hour_local` (default 7am) in your
local timezone (`GARDEN_TIMEZONE` in `secrets.env`). The brief is written by the LLM and covers:

- Today's weather (temperature, rain chance, conditions) via Open-Meteo — no API key needed
- Current moisture level in each bed and how it's trending
- A watering plan: skip/delay if rain is expected; water deeper in a heatwave; estimate hose minutes otherwise

Proving the system is alive is a side effect of the brief itself. Disable with `daily_brief.enabled: false` in `config.yaml`.

### Weather & location

Set these in `secrets.env` (not `config.yaml` — the repo is public):

```
GARDEN_ZIPCODE=78701
GARDEN_COUNTRY=us
GARDEN_TIMEZONE=America/Chicago
```

Or provide coordinates directly to skip geocoding:

```
GARDEN_LAT=30.2672
GARDEN_LON=-97.7431
```

Forecast is fetched from [Open-Meteo](https://open-meteo.com/) — free, no account needed.
It is cached for `weather.cache_minutes` (default 120 min) to avoid repeated calls.

### Watering advice in alerts

When a soil-moisture rule fires during the day, the alert message includes a hose-duration
estimate (~12 L/min assumed) adjusted for the moisture deficit and the weather forecast.
If meaningful rain is expected within a few hours, the message says to wait.

## Tuning thresholds

All values live in `config.yaml` — edit and restart the app:

```bash
vim ~/apps/garden-agent/config.yaml
sudo systemctl restart garden-agent
```
