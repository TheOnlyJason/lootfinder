# Running PriceWatch 24/7 on a Mac (launchd)

This keeps the bot running in the background, restarts it if it crashes, and
starts it automatically when you log in — so it posts price drops without you
doing anything.

> Before installing the service, make sure `.env` is filled in and the bot runs
> manually at least once: `source .venv/bin/activate && python bot.py`. Quit it
> with Ctrl+C once you see `PriceWatch online`.

The paths in [`com.pricewatch.bot.plist`](com.pricewatch.bot.plist) assume the
project lives at `/Users/jasondai/lootfinder`. If yours differs, edit the four
paths in that file first.

## Install

```bash
# 1. Link the service file into LaunchAgents
cp deploy/com.pricewatch.bot.plist ~/Library/LaunchAgents/

# 2. Load + start it
launchctl load ~/Library/LaunchAgents/com.pricewatch.bot.plist

# 3. Confirm it's running (look for com.pricewatch.bot with a PID, exit code 0)
launchctl list | grep pricewatch
```

It's now running and will relaunch on crash and at every login.

## Watch the logs

```bash
tail -f pricewatch.log
```

You should see `Synced N command(s) ...` and `PriceWatch online as ...`.

## Update after pulling new code

```bash
launchctl kickstart -k gui/$(id -u)/com.pricewatch.bot
```

## Stop / uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.pricewatch.bot.plist
rm ~/Library/LaunchAgents/com.pricewatch.bot.plist
```

## Notes

- The Mac must be **awake** to check prices. If the lid is closed / it's asleep,
  the loop is paused. For a true always-on setup, keep it plugged in and set
  System Settings → Battery/Lock Screen so it doesn't sleep, or use a cheap
  cloud host (Railway / Render / Fly.io) instead.
- If `launchctl list` shows a non-zero exit code, check `pricewatch.log` — it's
  almost always a missing value in `.env`.
