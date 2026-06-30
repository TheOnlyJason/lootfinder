# Running PriceWatch on a Mac (launchd)

Big retailers (B&H, Best Buy, ...) block automated requests from cloud/datacenter
IPs, so a free cloud runner like GitHub Actions gets `403`-ed. Your Mac's home
(residential) IP isn't blocked — so the reliable free option is to run the
checker on your Mac. `launchd` runs it on a schedule and at login.

There are two ways to run it. **Most people want Option A.**

---

## Option A — Scheduled checker + webhook (recommended)

Runs [`check_once.py`](../check_once.py) every hour. It reads
[`watches.json`](../watches.json), posts drops to a Discord **webhook**, and
exits. No bot token, no slash commands. Reuses the webhook + watches.json you
already set up.

The paths in [`com.pricewatch.check.plist`](com.pricewatch.check.plist) assume the
project is at `/Users/jasondai/lootfinder` — edit them if yours differs.

### 1. Put the webhook in a local `.env`

```bash
cp .env.example .env        # if you don't have one yet
```
Edit `.env` and set just this line (get the URL from your Discord channel →
Edit Channel → Integrations → Webhooks):
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

### 2. Test it once by hand

```bash
source .venv/bin/activate
python check_once.py
```
You should see each item's price and `Done — checked N item(s), posted ...`.
(First run records prices and posts nothing — that's expected.)

### 3. Install the hourly schedule

```bash
cp deploy/com.pricewatch.check.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pricewatch.check.plist
launchctl list | grep pricewatch        # shows com.pricewatch.check
tail -f pricewatch.log                   # watch each hourly run
```

Manage products by editing `watches.json` — no restart needed, the next run
picks it up. To stop it:
```bash
launchctl unload ~/Library/LaunchAgents/com.pricewatch.check.plist
```

---

## Option B — Always-on bot with slash commands

Runs [`bot.py`](../bot.py) as a persistent process so `/watch`, `/list_watches`,
etc. work in Discord. Needs `DISCORD_TOKEN` + `DISCORD_CHANNEL_ID`
(+ `DISCORD_GUILD_ID`) in `.env`, and the bot invited with the
`applications.commands` scope.

```bash
# Test first: `source .venv/bin/activate && python bot.py` until you see
# "PriceWatch online", then Ctrl+C.
cp deploy/com.pricewatch.bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pricewatch.bot.plist
launchctl list | grep pricewatch
```
Reload after pulling new code: `launchctl kickstart -k gui/$(id -u)/com.pricewatch.bot`.
Uninstall: `launchctl unload ~/Library/LaunchAgents/com.pricewatch.bot.plist`.

---

## Notes

- **The Mac must be awake to check.** If it's asleep, `launchd` runs the missed
  job when it wakes. For round-the-clock coverage keep it plugged in with
  System Settings → Lock Screen / Battery set to not sleep.
- Both options log to `pricewatch.log` in the project folder (`tail -f` it).
- If a run shows a non-zero exit, the log almost always points to a missing value
  in `.env`.
