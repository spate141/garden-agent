# Sensor Reference

All sensor keys, units, hardware source, and notes.

## Channel map

| Sensor key | Label | Unit | Hardware | Notes |
|------------|-------|------|----------|-------|
| `soilmoisture1` | Bed 1 soil moisture | % | WH51 ch1 | Relative %, not volumetric. Rules express drying trends, not absolute science values. |
| `soilmoisture2` | Bed 2 soil moisture | % | WH51 ch2 | Same as above. |
| `soilbatt1` | WH51 ch1 battery | V | WH51 ch1 | Nominal ~1.5V. Alert fires below 1.1V. |
| `soilbatt2` | WH51 ch2 battery | V | WH51 ch2 | Same as above. |
| `tempc` | Outdoor temperature | °C | WN31 | Converted from °F at ingest. |
| `humidity` | Outdoor humidity | % | WN31 | Raw value, no conversion. |
| `tempinc` | Indoor temperature | °C | GW1200 internal | Converted from °F at ingest. |
| `humidityin` | Indoor humidity | % | GW1200 internal | Raw value. |
| `baromrel_hpa` | Relative pressure | hPa | GW1200 | Converted from inHg at ingest. |

## Adding new sensors

Add a new entry to `config.yaml` under `sensors:` with a `label` and `unit`. No code changes needed — the ingest parser and dashboard pick it up automatically.

```yaml
sensors:
  soilmoisture3:
    label: "Bed 3 soil moisture"
    unit: "%"
```

## Unit conversions (applied at ingest)

| Raw Ecowitt field | Stored as | Conversion |
|-------------------|-----------|------------|
| `tempf` | `tempc` | `(°F - 32) × 5/9` |
| `tempinf` | `tempinc` | `(°F - 32) × 5/9` |
| `baromrelin` | `baromrel_hpa` | `inHg × 33.8639` |
| `soilmoisture1..8` | `soilmoisture1..8` | None (already %) |
| `soilbatt1..8` | `soilbatt1..8` | None (already V) |

Raw POST payload is always stored in `snapshots.raw_json` for audit.
