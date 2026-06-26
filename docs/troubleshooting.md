# Troubleshooting

## Dashboard / API

**`garden.snehal.ai` not loading**
```bash
# Check tunnel is running
sudo systemctl status cloudflared

# Check app is running
sudo systemctl status garden-agent
curl -s localhost:8001/health
```

**Dashboard shows "no data yet"**
- No readings in the DB. Send a fake POST to verify the pipeline:
  ```bash
  cd ~/apps/garden-agent && bash scripts/fake_post.sh
  ```
- If GW1200 is wired, check the customized server config matches `INGEST_PASSKEY` in `secrets.env`.

---

## Ingest

**POST returns 401**
- `PASSKEY` in the GW1200 config doesn't match `INGEST_PASSKEY` in `secrets.env`.

**POST returns 422 / missing fields**
- Protocol must be set to **Ecowitt** (not WeatherUnderground) in the GW1200 config.

---

## Telegram

**No alerts arriving**
```bash
# Verify bot token and chat ID
bash scripts/tg_test.sh

# Check runner logs
sudo journalctl -u garden-agent -n 50
```

**Alerts firing too often**
- Increase the relevant cooldown in `config.yaml` and restart the app.

**Alerts not firing when they should**
- Check `alert_state` table — the rule may still be marked `active` from a previous fire within its cooldown window:
  ```bash
  sqlite3 $DB_PATH "SELECT * FROM alert_state;"
  ```
- To reset a stuck rule manually:
  ```bash
  sqlite3 $DB_PATH "DELETE FROM alert_state WHERE rule_id = 'soil_moisture_low:soilmoisture1';"
  ```

---

## systemd services

**garden-agent not starting**
```bash
sudo journalctl -u garden-agent -n 100
# Common cause: secrets.env missing a required key
```

**cloudflared not connecting**
```bash
sudo journalctl -u cloudflared -n 50
# Common cause: tunnel credentials missing from /etc/cloudflared/
ls /etc/cloudflared/
```

**Cron tick not running**
```bash
sudo systemctl list-timers garden-cron.timer
sudo journalctl -u garden-cron -n 30
```

---

## Database

**Check DB size**
```bash
du -sh $DB_PATH
sqlite3 $DB_PATH "SELECT COUNT(*) FROM readings;"
```

**Run a manual backup**
```bash
cd ~/apps/garden-agent && bash scripts/backup_db.sh
```

**Check backup history**
```bash
ls -lh ~/apps/garden-agent/backups/
```

---

## VM

**Check memory usage (e2-micro has 1GB shared with trading bot)**
```bash
free -h
```

**Restart all garden services**
```bash
sudo systemctl restart garden-agent cloudflared
```

**View live logs**
```bash
sudo journalctl -u garden-agent -f
sudo journalctl -u cloudflared -f
```
