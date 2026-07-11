<p align="center">
  <img src="logo.png" width="96" alt="garden-agent" />
</p>

# garden-agent

Self-hosted pipeline: ingests Ecowitt soil/air sensor data from a GW1200 gateway, stores it in SQLite, serves a dashboard at `your.domain.com`, and runs a deterministic-rules agent that calls Claude to write Telegram alerts and a daily morning brief.

<p align="center">
  <img src="docs/architecture.svg" alt="garden-agent architecture" width="900">
</p>

## Dashboard Screenshots

<details>
<summary>Click here to see screenshot...</summary>

![Dashboard – light mode](docs/light.png)

</details>

## Dashboard: metrics & sensor data

Everything on the dashboard traces back to `config.yaml` (`sensors:`, `dashboard.beds`, `thresholds:`) — add a sensor or bed there and it shows up with no code changes.

**Bed status chips** — one per bed, computed by `garden/derived.py:bed_stress()`:
- **Dry / OK / Wet**, judged against each bed's own moisture band — self-learned from that bed's last 7 days of watering cycles (`learned_moisture_band`), falling back to a crop-derived range (e.g. tomato 50–80%) until a bed has enough history. This keeps compaction differences between beds (loose new soil reads lower than settled old soil for the same actual water) from producing a false "Dry" reading.
- **Cold / Heat** stress, from outdoor air temp vs. each bed's crop temperature range.
- Battery-low warning per bed (WH51 soil sensor voltage).

**"When to water next"** — per-bed forecast card (`garden/main.py:_bed_watering_forecast`):
- Projects each bed's current drydown rate (`derived.drydown_rate`, a Theil-Sen slope over the trailing 48h, excluding watering spikes) forward to that bed's own dry threshold (`derived.days_until_dry`).
- Shows `~N days` / `today` / `just watered` (while a recent watering event is still draining) plus a ☔ badge when rain is forecast in the next 12h and a bed is due soon.

**Per-bed soil-moisture trend charts** (1h/3h/12h/24h/7d) — raw `soilmoisture1..4` readings, with a green "healthy" band and a red "too wet" line drawn from the same self-learned/crop band the chips use.

**Climate strip** — one verdict sentence + three headline stats:
- **VPD** (vapor-pressure deficit, kPa) — classified low / healthy / high / very-high stress via `derived.vpd_status()` against `derived.thresholds` in `config.yaml`.
- **Feels-like temp** — NWS heat-index formula (`derived.heat_index_f`).
- **Water balance** — daily rainfall minus reference evapotranspiration (ET₀) from the Open-Meteo forecast (`derived.et0_water_balance`); negative means the garden is running a deficit.
- Frost risk is flagged from dew point (`derived.frost_risk`) whenever it's near/below freezing.

**Grouped trend charts** — Temperature (outdoor + gazebo), Humidity (outdoor + gazebo), and VPD (with its low/healthy/high/very-high zone bands). Any sensor with `chart: true` in `config.yaml` can be added to a group in `garden.js`'s `TRENDS_GROUPS`.

**Stat groups** (compact numeric tiles, no chart) — Bed 1–4 (moisture + battery), Gazebo (temp/humidity), Station (outdoor + indoor temp/humidity, barometric pressure), Air (VPD, dew point, feels-like).

**Sky/garden hero animation** — sun/moon position and cloud state driven by Open-Meteo sunrise/sunset and current conditions, purely visual (no metric backing it beyond what's already in the climate strip).

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Ecowitt GW1200 gateway (or use `scripts/fake_post.sh` to simulate)

## Install

```bash
git clone <repo-url> garden-agent
cd garden-agent
uv sync
```

## Configure

```bash
cp secrets.env.example secrets.env
chmod 600 secrets.env
$EDITOR secrets.env
```

Required values in `secrets.env`:

| Key | Description |
|-----|-------------|
| `INGEST_PASSKEY` | Any random string; set the same value in the GW1200 custom-server config. `openssl rand -hex 16` works. |
| `TELEGRAM_BOT_TOKEN` | From @BotFather. See [docs/telegram.md](docs/telegram.md). |
| `TELEGRAM_CHAT_ID` | Your personal chat ID. See [docs/telegram.md](docs/telegram.md). |
| `ANTHROPIC_API_KEY` | From [console.anthropic.com](https://console.anthropic.com/settings/keys). Used for alert prose (Stage 8). |
| `DB_PATH` | SQLite file path. Default: `garden.sqlite3`. |

Sensor thresholds, cooldowns, and channel labels live in `config.yaml` — edit freely, no code changes needed.

## Run (local dev)

```bash
uv run uvicorn garden.main:app --reload
```

- Dashboard: http://127.0.0.1:8000
- Health: http://127.0.0.1:8000/health
- Ingest: `POST http://127.0.0.1:8000/api/ecowitt`

Simulate a sensor POST:

```bash
./scripts/fake_post.sh
```

Trigger low-moisture values (trips the alert rule):

```bash
LOW=1 ./scripts/fake_post.sh
```

Test Telegram delivery:

```bash
./scripts/tg_test.sh
```

## Local Simulation

For a live-feeling dashboard without real hardware, `scripts/dev.sh` starts the
server and streams a continuous, slowly-drifting sensor feed into it in one
command:

```bash
./scripts/dev.sh
```

This sets `GARDEN_DRY_RUN=1`, which suppresses real Telegram sends and
Anthropic API calls — alerts and the daily brief are logged instead of sent,
so you can exercise the full pipeline safely offline. Extra args are passed
through to `simulate.py`, e.g. a faster tick rate:

```bash
./scripts/dev.sh --interval 5
```

`simulate.py` can also be run standalone against a server you started yourself:

```bash
uv run python scripts/simulate.py --host http://127.0.0.1:8000 --interval 10
```

Simulated values random-walk within healthy ranges, so deterministic alert
rules won't normally fire — `GARDEN_DRY_RUN=1` is a backstop in case a walk
drifts past a threshold anyway. To manually set `GARDEN_DRY_RUN=1` in a
one-off shell instead, add `GARDEN_DRY_RUN=1` to `secrets.env` (see
`secrets.env.example`).

## Deploy (GCP e2-micro + Cloudflare Tunnel)

```bash
# On the VM — replace YOUR_VM_USER with your username
git clone <repo-url> ~/apps/garden-agent
cd ~/apps/garden-agent
uv sync
cp secrets.env.example secrets.env && nano secrets.env && chmod 600 secrets.env

# Edit the service file, then install
sed -i 's/YOUR_VM_USER/'"$USER"'/g' systemd/garden-agent.service
sudo cp systemd/garden-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now garden-agent
curl -s localhost:8001/health
```

Cloudflare Tunnel (`your.domain.com` → `localhost:8001`):

```bash
# Install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /tmp/cloudflared && sudo install /tmp/cloudflared /usr/local/bin/cloudflared

# Authenticate + create tunnel
cloudflared tunnel login
cloudflared tunnel create garden
cloudflared tunnel route dns garden your.domain.com

# Write ~/.cloudflared/config.yml:
#   tunnel: <TUNNEL_ID>
#   credentials-file: /home/<user>/.cloudflared/<TUNNEL_ID>.json
#   ingress:
#     - hostname: your.domain.com
#       service: http://localhost:8001
#     - service: http_status:404

sudo cloudflared service install
sudo systemctl enable --now cloudflared

# Verify
curl https://your.domain.com/health
```

Cron tick (watchdog + heartbeat, every 15 min):

```bash
sed -i 's/YOUR_VM_USER/'"$USER"'/g' systemd/garden-cron.service
sudo cp systemd/garden-cron.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now garden-cron.timer
```

Nightly DB backup (02:00 UTC, keeps last 7 days locally + pushes to Cloudflare R2):

```bash
sed -i 's/YOUR_VM_USER/'"$USER"'/g' systemd/garden-backup.service
sudo cp systemd/garden-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now garden-backup.timer
```

R2 offsite backup is optional — add these to `secrets.env` to enable (see [docs/deploy.md](docs/deploy.md) for setup):

| Key | Description |
|-----|-------------|
| `R2_ACCOUNT_ID` | Cloudflare account ID (R2 overview sidebar) |
| `R2_BUCKET` | Bucket name, e.g. `garden-backups` |
| `R2_ACCESS_KEY_ID` | From R2 → Manage API Tokens |
| `R2_SECRET_ACCESS_KEY` | From R2 → Manage API Tokens |

Also install rclone once: `curl https://rclone.org/install.sh | sudo bash`

Restore from R2 onto a fresh VM: `bash scripts/restore_db.sh`

Journal log rotation (cap at 200MB):

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp systemd/journald-garden.conf /etc/systemd/journald.conf.d/garden.conf
sudo systemctl restart systemd-journald
```

## Hardware Setup

Tested with: **GW1200** gateway + **WH51** soil moisture sensors + **WN31** multi-channel temp/humidity sensor. Other Ecowitt sensors that use the same HTTP push protocol will also work — add their fields to `_FIELD_MAP` in `garden/ingest.py`.

### What each device sends

| Device | Fields |
|--------|--------|
| GW1200 (built-in) | `tempinf`, `humidityin`, `baromrelin`, `baromabsin` |
| WH51 (per channel) | `soilmoisture1`…`soilmoisture8`, `soilbatt1`…`soilbatt8` |
| WN31 (per channel) | `temp1f`…`temp8f`, `humidity1`…`humidity8` |

### Step 1 — Connect the GW1200 to WiFi

Download **WSView Plus** (iOS / Android — not the older "WSView"). Open it → **+** → **Add Device** → **GW1200**. Follow the in-app steps to connect the gateway to your **2.4 GHz** WiFi. Once connected, the LED goes solid and the app shows the device with its local IP.

### Step 2 — Pair WH51 soil sensors

Do one at a time so you know which channel maps to which location.

1. WSView Plus → tap your GW1200 → **Sensor List** → **+**.
2. Hold the button on the WH51 until its LED flashes rapidly (~5 s) to enter pairing mode.
3. The app assigns the next available channel (ch1, ch2, …). Rename it immediately (e.g. "Bed 1").
4. Repeat for additional sensors.
5. Push probes into soil at ~45°, mostly buried, leaving the transmitter above ground (~4–6 inch depth).

Channel → storage key: ch1 → `soilmoisture1` / `soilbatt1`, ch2 → `soilmoisture2` / `soilbatt2`, etc.

### Step 3 — Set WN31 dip switches and pair

The WN31 has 3 dip switches on the back that set which channel (1–8) it broadcasts on. Set them before inserting batteries.

| Channel | SW1 | SW2 | SW3 |
|---------|-----|-----|-----|
| 1 | OFF | OFF | OFF |
| 2 | ON  | OFF | OFF |
| 3 | OFF | ON  | OFF |
| 4 | ON  | ON  | OFF |

Set to channel 1 (all OFF) → reports as `temp1f` / `humidity1`. Pair via WSView Plus the same way as WH51. **Note:** the WN31 is not rated for outdoor use — place it in a greenhouse, shed, or indoors.

### Step 4 — Point the GW1200 at your server

In **WSView Plus**: tap your GW1200 → gear icon → **Customized Server**. Fill in:

| Field | Value |
|-------|-------|
| Protocol | **Ecowitt** (not Wunderground) |
| Server IP / Hostname | `your.domain.com` |
| Path | `/api/ecowitt` |
| Port | `443` |
| Upload Interval | `60` seconds |
| PASSKEY | value of `INGEST_PASSKEY` from `secrets.env` |

Alternatively, use the GW1200's local web UI: `http://<gateway-local-IP>` → **Weather Services** → **Customized**.

### Step 5 — Verify

Wait 60–90 s, then:

```bash
curl https://your.domain.com/health
# "sensors_seen" should be > 0 and "last_reading_ts" a real timestamp
```

Check logs if nothing arrives:

```bash
sudo journalctl -u garden-agent -n 30
# Look for: "Parsed snapshot ts=..., N metrics"
# 401 means PASSKEY mismatch; 400 means unrecognised payload
```

### Wipe test data before going live

```bash
sudo systemctl stop garden-agent
rm ~/apps/garden-agent/garden.sqlite3   # schema auto-recreates on next start
sudo systemctl start garden-agent
```

Full step-by-step setup guide (including troubleshooting): [docs/hardware-setup.md](docs/hardware-setup.md)

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard |
| `GET` | `/health` | `{status, sensors_seen, last_reading_ts}` |
| `POST` | `/api/ecowitt` | Ecowitt-protocol ingest |
| `GET` | `/api/latest` | Latest value per sensor (JSON array) |
| `GET` | `/api/series?sensor=soilmoisture1&hours=24` | Time-series for one sensor |
| `GET` | `/api/insights` | Derived metrics: VPD, dew point/frost risk, heat index, water balance, per-bed stress + watering forecast |
