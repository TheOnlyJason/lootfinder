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

> **Heads-up on cloud hosting + scraping.** Big retailers (B&H, Best Buy, Apple,
> Micro Center, ...) block automated requests from datacenter IPs — so the `url`
> scraper returns `403` from GitHub Actions and most free cloud VMs. Scraping
> those sites reliably needs a **residential IP** (your own machine). Cloud
> hosting only works reliably with an **official API** source (e.g. `bestbuy`),
> which isn't IP-blocked.

There are two ways to run PriceWatch. Pick one:

### Mode A — Scheduled checker + webhook (free, recommended for scraping)

Runs [`check_once.py`](check_once.py) on a schedule, posts price drops to a
Discord **webhook**, no bot token needed. You manage products in
[`watches.json`](watches.json). Run it **on your Mac** (residential IP, so the
scraper isn't blocked) via `launchd` — see [deploy/README.md](deploy/README.md),
Option A.

1. **Make a webhook:** Discord channel → Edit Channel → Integrations → Webhooks →
   New Webhook → Copy URL. Put it in `.env` as `DISCORD_WEBHOOK_URL`.
2. **Edit [`watches.json`](watches.json)** — a list of items; omit `target_price`
   to alert on any decrease:
   ```json
   [
     { "source": "url", "identifier": "https://store.example/mac", "label": "Mac mini", "target_price": 899 }
   ]
   ```
3. **Schedule it** with the `launchd` agent in `deploy/`.

(The same script runs on GitHub Actions cron too — fine for an `api` source or
stores that don't block cloud IPs, but B&H-style retailers will `403` there.)

### Mode B — Always-on bot (slash commands)

Runs [`bot.py`](bot.py) as a persistent process, so `/watch` and friends work.
Needs `DISCORD_TOKEN` + `DISCORD_CHANNEL_ID` in `.env`. Run it on your Mac under
`launchd` ([deploy/README.md](deploy/README.md), Option B), or on a cloud host if
you only use API-based sources.

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
| [`storage.py`](storage.py)    | Atomic `watchlist.json` read/write (Mode B)             |
| [`check_once.py`](check_once.py) | One-shot check → Discord webhook (Mode A)             |
| [`watches.json`](watches.json) | Product list for Mode A (you edit this)                |
| [`deploy/`](deploy)           | `launchd` agents + setup guide for running on a Mac     |
| [`check_url.py`](check_url.py) | CLI to test whether the scraper can read a price from a URL |
| [`tests/test_offline.py`](tests/test_offline.py) | Offline checks for parsing + alert logic (no token needed) |

## Notes & caveats

- Respect each site's Terms of Service and `robots.txt`. Keep the interval
  reasonable (hourly is fine; don't hammer pages every minute).
- The generic scraper depends on sites exposing structured data and not blocking
  bots. **Whether a retailer works depends on your IP**, so test from the machine
  that will run the bot: `python check_url.py <product-url>`. Observed for Macs:
  **B&H works** (clean JSON-LD), **Micro Center** loads but renders its price in
  JavaScript (nothing to scrape), **Best Buy / Apple** block automated requests
  (use the official Best Buy API for those). Smaller / Shopify-based stores tend
  to work best.
- This stores your watchlist in a local `watchlist.json`. Back it up if it
  matters to you.
