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
