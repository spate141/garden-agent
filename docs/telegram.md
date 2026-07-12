# Telegram bot setup

Two things needed: a **bot token** (from BotFather) and your **personal chat ID**.
Both go in `secrets.env`. Takes about 3 minutes.

---

## 1. Create the bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Choose a name (e.g. `Garden Monitor`) and a username (e.g. `my_garden_bot` — must end in `bot`).
4. BotFather replies with your token:
   ```
   123456789:ABCdefGhIjKlmNoPQRsTuVwXyz
   ```
   Copy it — this is `TELEGRAM_BOT_TOKEN`.

---

## 2. Get your chat ID

1. Send any message to your new bot (e.g. "hello").
2. Fetch the update:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool
   ```
   Look for `"chat": { "id": 987654321 }` in the response.
   That number is `TELEGRAM_CHAT_ID`.

   If the result is empty (`"result": []`), send another message to the bot first, then retry.

---

## 3. Fill in secrets.env

```bash
# In your project directory (local or on the VM):
cp secrets.env.example secrets.env
nano secrets.env
```

Fill in:
```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIjKlmNoPQRsTuVwXyz
TELEGRAM_CHAT_ID=987654321
```

Then lock the file:
```bash
chmod 600 secrets.env
```

---

## 4. Test

Once the app is running locally:
```bash
./scripts/tg_test.sh
```

Or directly:
```bash
curl -s -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  --data-urlencode "chat_id=<CHAT_ID>" \
  --data-urlencode "text=🌱 Garden — test ping" \
  | python3 -m json.tool
```

A message should appear on your phone within a few seconds.

---

## What Claude Code needs from you

Once you have the bot token and chat ID, add them to `secrets.env`.
The `tg_test.sh` script will confirm delivery end-to-end.
No further input needed — the Telegram code is already written.

---

## 5. Inbound bot commands (/bed1, /beds, /weather, /air, /brief, /deploy)

The bot can also answer commands on demand — tap `/bed4` in Telegram and get a
summary of Bed 4's moisture, battery, and crops back within a second. This needs
one more secret and a public URL Telegram can reach; the app already exposes one
via Cloudflare Tunnel (`docs/deploy.md`).

### Setup

1. Generate a webhook secret:
   ```bash
   openssl rand -hex 16
   ```
2. Add to `secrets.env`:
   ```
   TELEGRAM_WEBHOOK_SECRET=<the hex string>
   GARDEN_PUBLIC_URL=https://garden.snehal.ai
   ```
3. Register the webhook + command menu with Telegram:
   ```bash
   uv run python -m garden.bot --setup
   ```
   This is idempotent — safe to re-run any time (e.g. after adding/renaming a bed
   in `config.yaml`, to refresh the command menu). `deploy.sh` runs it automatically
   on every deploy once both env vars above are set.

### Using it

Open the bot's chat in Telegram and tap the **menu button** (bottom-left, next to
the message box) to see all commands, or type them directly:

| Command | Reply |
|---|---|
| `/bed1` … `/bedN` | That bed's moisture %, crop-stress status, battery, last-updated |
| `/beds` | One-line summary per bed |
| `/weather` | Today's forecast + current conditions |
| `/air` | VPD, dew point / frost risk, feels-like |
| `/brief` | Sends the morning brief immediately (bypasses the 7am schedule) |
| `/deploy` | Runs `deploy.sh` on the VM: git pull, restart services. See below. |
| `/help` | Lists all available commands |

### `/deploy`

Runs `deploy.sh` on the VM — the same script you'd otherwise SSH in and run by
hand after pushing to `main`. Because `deploy.sh` restarts `garden-agent`
itself partway through, `garden/bot.py` launches it as an independent
transient systemd unit (`systemd-run --unit=garden-deploy --collect`) instead
of a plain subprocess — a plain child process would share this service's
cgroup and get killed by that restart before finishing the remaining steps
(cron/backup timers, webhook re-registration, status summary). The bot
replies immediately ("Deploy started…"); `deploy.sh` itself posts a "Deploy
complete ✅" or "Deploy FAILED" message straight to Telegram (via `curl`,
using the same `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` from `secrets.env`)
once it finishes, since the process that queued it doesn't survive to report
back.

**One-time VM setup required**: `deploy.sh`'s `sudo` calls (`systemctl
restart`/`enable`/`reset-failed`, `tee` into `/etc/systemd/system/`) prompt
for a password when run interactively — fine over SSH, but there's no
terminal to answer that prompt when Telegram triggers it. Add passwordless
sudo for your deploy user (`sudo visudo -f /etc/sudoers.d/garden-deploy`):

```
your_vm_user ALL=(root) NOPASSWD: /usr/bin/systemctl, /usr/bin/tee, /usr/bin/systemd-run
```

**Security note**: this is a materially bigger capability than the other
commands — anyone who can message the authorized `TELEGRAM_CHAT_ID` can
trigger a `git pull` of whatever is on `main` plus a root-level systemd
restart. The existing owner-chat-id check in `garden/bot.py` is the only
gate, same as every other command; make sure that chat is one you trust.

### How it works

Telegram POSTs each command to `/api/telegram` on the running app (`garden/main.py`).
That route is disabled (404) unless `TELEGRAM_WEBHOOK_SECRET` is set, and only
accepts requests carrying Telegram's own secret-token header — a second check in
`garden/bot.py` also drops anything not from `TELEGRAM_CHAT_ID`, so no one else can
use the bot even if they find the webhook URL. Reply logic lives in `garden/bot.py`
and reuses the same storage/derived/weather helpers as the dashboard and alerts —
no separate data path to keep in sync.

### Troubleshooting

- Command menu not showing / stale after editing beds in `config.yaml`: re-run
  `uv run python -m garden.bot --setup`.
- No reply at all: check `sudo journalctl -u garden-agent -n 50` on the VM for
  `garden.bot` / `garden.telegram` log lines; confirm `curl -sf https://garden.snehal.ai/health`
  works (the webhook route depends on the same running service).
- `getWebhookInfo` shows the currently registered webhook + any delivery errors:
  ```bash
  curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo" | python3 -m json.tool
  ```
- `/deploy` replied but nothing happened / no completion message: check
  `sudo journalctl -u garden-deploy -n 100` for the transient unit's own
  output, and `cat deploy.log` in the project directory (deploy.sh's stdout/
  stderr are appended there when launched this way). A "Failed to start the
  deploy" reply means `sudo systemd-run` itself couldn't launch — usually the
  passwordless-sudo entry above is missing.
