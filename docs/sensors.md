# Sensor Reference

All sensor keys, units, hardware source, and notes.

## Channel map

| Sensor key | Label | Unit | Hardware | Notes |
|------------|-------|------|----------|-------|
| `soilmoisture1` | Bed 1 soil moisture | % | WH51 ch1 | Relative %, not volumetric. Rules express drying trends, not absolute science values. |
| `soilmoisture2` | Bed 2 soil moisture | % | WH51 ch2 | Same as above. |
| `soilbatt1` | WH51 ch1 battery | V | WH51 ch1 | Nominal ~1.5V. Alert fires below 1.1V. |
| `soilbatt2` | WH51 ch2 battery | V | WH51 ch2 | Same as above. |
| `temp_f` | Outdoor temperature | °F | WN31 | Stored as-is (no conversion). |
| `humidity` | Outdoor humidity | % | WN31 | Raw value, no conversion. |
| `temp_in_f` | Indoor temperature | °F | GW1200 internal | Stored as-is (no conversion). |
| `humidityin` | Indoor humidity | % | GW1200 internal | Raw value. |
| `baromrel_inhg` | Relative pressure | inHg | GW1200 | Stored as-is (no conversion). |

## Adding new sensors

Add a new entry to `config.yaml` under `sensors:` with a `label` and `unit`. No code changes needed — the ingest parser and dashboard pick it up automatically.

```yaml
sensors:
  soilmoisture3:
    label: "Bed 3 soil moisture"
    unit: "%"
```

## Field name mapping (applied at ingest)

All values are stored in US customary units — no conversion applied. The Ecowitt station sends in °F, inHg, and mph natively.

| Raw Ecowitt field | Stored as | Unit |
|-------------------|-----------|------|
| `tempf` | `temp_f` | °F |
| `tempinf` | `temp_in_f` | °F |
| `baromrelin` | `baromrel_inhg` | inHg |
| `windspeedmph` | `windspeed_mph` | mph |
| `windgustmph` | `windgust_mph` | mph |
| `rainratein` | `rainrate_inh` | in/h |
| `dailyrainin` | `rain_daily_in` | in |
| `soilmoisture1..8` | `soilmoisture1..8` | % |
| `soilbatt1..8` | `soilbatt1..8` | V |

Raw POST payload is always stored in `snapshots.raw_json` for audit.
