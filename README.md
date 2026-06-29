# PriceWatch — a Discord price-drop bot

An autonomous Discord bot that checks product prices on a fixed interval
(default hourly) and posts an alert when something drops to/below your target
price. Add or remove products from inside Discord with slash commands. Ships
with a Best Buy (SKU) source and a generic product-page scraper, and is built so
you can bolt on more sources easily.

## The one decision that matters: where prices come from

This is where these projects live or die, so read this part.

**Use an official API wherever one exists.** Big retailers (Amazon, Best Buy's
storefront, Apple) detect and block automated scraping aggressively, change
their HTML constantly, and forbid scraping in their Terms of Service. Scraping
them directly will be unreliable and is against their rules.

- **Best Buy** publishes a free Products API (developer.bestbuy.com). You query
  by SKU and get the live price back as clean JSON. This is the robust path for
  the Mac mini / Mac Studio SKUs and any other Best Buy product. Register for a
  key, drop it in `.env`, and use `source: bestbuy`. (API access is
  approval-based and Best Buy has changed its terms over time, so confirm
  current availability when you sign up.)
- **Amazon:** no free price API. The realistic option is the Keepa API (paid) —
  it's built exactly for price-history/drop tracking. Add a `fetch_keepa()`
  function alongside the others in [`sources.py`](sources.py) and register it in
  `SOURCES` / `resolve()`.
- **Smaller retailers** (Micro Center, B&H, many Shopify stores): the included
  generic `url` source reads schema.org product data (JSON-LD) or a price meta
  tag, which a lot of these sites expose. Best-effort, but often works.

In short: `bestbuy` for Best Buy, Keepa for Amazon, `url` for the long tail.

## Setup (about 10 minutes)

1. **Create the bot application**
   - Go to <https://discord.com/developers/applications> → New Application.
   - Bot tab → reset/copy the token.
   - Under "Privileged Gateway Intents" nothing extra is needed (we only use
     default intents + slash commands).
   - OAuth2 → URL Generator → scopes `bot` + `applications.commands`, bot
     permission `Send Messages`. Open the generated URL to invite the bot to your
     server.

2. **Get the channel ID**
   - In Discord: Settings → Advanced → enable Developer Mode.
   - Right-click the target channel → Copy Channel ID.

3. **Configure**

   ```bash
   cp .env.example .env
   # fill in DISCORD_TOKEN, DISCORD_CHANNEL_ID, (optional) BESTBUY_API_KEY
   ```

4. **Install + run**

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python bot.py
   ```

## Using it

Slash commands (all replies are private/ephemeral):

- `/watch source:<Best Buy|Generic URL> identifier:<SKU or URL> label:<name> [target_price:<number>]`
  - Best Buy: `source: Best Buy`, `identifier: 6565581`, `label: Mac mini M4`, `target_price: 450`
  - Generic: `source: Generic URL`, `identifier: https://store.example/mac-studio`, `label: Mac Studio`
  - Leave `target_price` off to be alerted on any price decrease.
- `/list_watches` — show everything you're watching and the last seen price.
- `/unwatch item_id:<id>` — stop watching an item.
- `/checknow` — force an immediate check of everything.

The bot also checks automatically every `CHECK_INTERVAL_HOURS`. It de-dupes
alerts so you won't get pinged every hour for the same price — only when it
drops to a new low (or first crosses your target).

## Making it truly autonomous (hosting)

Because the bot is interactive (it listens for slash commands) and runs a
background loop, it needs to stay running. Options, cheapest first:

- **Your Mac or a Raspberry Pi, always on.** Run it under `launchd` (macOS) or
  `systemd` (Linux) so it restarts on boot/crash. Simplest if you've got a
  machine that's on anyway.
- **A small cloud host:** Railway, Render (Background Worker), or Fly.io. Push
  the repo, set the env vars in their dashboard, done. Free/cheap tiers are
  plenty for one bot.
- **Cheap VPS** (e.g. a $5 droplet) with `systemd` + the venv.

### Alternative: no always-on server (webhook + cron)

If you only want notifications and don't care about slash commands, you can skip
the persistent bot entirely:

- Use a Discord incoming webhook (Channel Settings → Integrations → Webhooks).
- Write a small script that checks prices once and POSTs to the webhook URL.
- Schedule it hourly with GitHub Actions (a `schedule:` cron workflow, free) or
  plain `cron`. State (last prices) can live in the repo or a tiny gist.

This is the lowest-maintenance setup; you lose the in-Discord `/watch` UX and
manage the product list in a file instead.

## Extending it

To add a source, write one async function returning a `PriceResult` and register
it in `SOURCES` / `resolve()` in [`sources.py`](sources.py). To add product
types beyond Macs, nothing special is needed — just `/watch` any SKU or URL.

## Project layout

| File                          | What it does                                            |
| ----------------------------- | ------------------------------------------------------- |
| [`bot.py`](bot.py)            | Discord client, slash commands, the hourly check loop   |
| [`sources.py`](sources.py)    | Price sources (`bestbuy`, `url`) + the `resolve()` registry |
| [`alerts.py`](alerts.py)      | New-low / target-crossing de-dupe logic                 |
| [`storage.py`](storage.py)    | Atomic `watchlist.json` read/write                      |
| [`tests/test_offline.py`](tests/test_offline.py) | Offline checks for parsing + alert logic (no token needed) |

## Notes & caveats

- Respect each site's Terms of Service and `robots.txt`. Keep the interval
  reasonable (hourly is fine; don't hammer pages every minute).
- The generic scraper depends on sites exposing structured data; some won't, and
  some will block automated requests regardless of user agent.
- This stores your watchlist in a local `watchlist.json`. Back it up if it
  matters to you.
