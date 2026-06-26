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
| `thresholds.temp_frost.below` | `2.0` | °C threshold (warns before 0°C) |
| `cooldowns.temp_frost_minutes` | `60` | Re-alert suppression window |

Sensors: `tempc`
Cadence: instant (fires on POST)

---

### `temp_heat`

Fires when outdoor temperature exceeds heat stress threshold.

| Config key | Default | Description |
|------------|---------|-------------|
| `thresholds.temp_heat.above` | `38.0` | °C threshold |
| `cooldowns.temp_heat_minutes` | `60` | Re-alert suppression window |

Sensors: `tempc`
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

## Heartbeat

A daily alive-ping is sent at `heartbeat.hour_utc` (default 12:00 UTC) reporting sensor count and last reading timestamp. Disable with `heartbeat.enabled: false` in `config.yaml`.

## Tuning thresholds

All values live in `config.yaml` — edit and restart the app:

```bash
vim ~/apps/garden-agent/config.yaml
sudo systemctl restart garden-agent
```
