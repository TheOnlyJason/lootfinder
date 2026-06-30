#!/usr/bin/env python3
"""One-shot price check for the serverless (GitHub Actions + webhook) mode.

Reads the product list from ``watches.json``, compares each price against the
de-dupe state in ``state.json``, and POSTs an embed to a Discord **webhook** for
anything that dropped to a new low or first crossed its target. Then it rewrites
``state.json`` (which the workflow commits back, so de-duping survives runs).

No bot token, no always-on server — meant to be run on a schedule by
``.github/workflows/pricewatch.yml``. Run locally with:

    DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python check_once.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp
from dotenv import load_dotenv

import alerts
import sources

# Local runs read DISCORD_WEBHOOK_URL from .env; in GitHub Actions it comes from
# the environment, and load_dotenv() is a harmless no-op when there's no .env.
load_dotenv()

WATCHES_PATH = os.getenv("WATCHES_PATH", "watches.json")
STATE_PATH = os.getenv("STATE_PATH", "state.json")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# When truthy, post a summary of ALL current prices every run (a heartbeat),
# instead of staying silent until something drops.
ALWAYS_NOTIFY = os.getenv("ALWAYS_NOTIFY", "").strip().lower() in ("1", "true", "yes", "on")

# Seconds to pause between item fetches, so we don't burst a retailer into
# rate-limiting / 403s. Bump it if you watch many items at one store.
INTER_ITEM_DELAY = float(os.getenv("INTER_ITEM_DELAY", "1.5"))

_CURRENCY_SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "C$", "AUD": "A$"}
_GREEN = 0x2ECC71
_BLURPLE = 0x5865F2


def _key(watch: dict) -> str:
    """Stable identity for a watch, so state maps even if label/target change."""
    return f"{watch.get('source')}:{watch.get('identifier')}"


def _fmt(price, currency: str = "USD") -> str:
    if price is None:
        return "—"
    symbol = _CURRENCY_SYMBOLS.get((currency or "USD").upper())
    return f"{symbol}{price:,.2f}" if symbol else f"{price:,.2f} {currency}"


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def build_payload(watch: dict, result: "sources.PriceResult", item: dict, reasons: list[str]) -> dict:
    """Build the Discord webhook JSON body for a drop alert."""
    fields = [{"name": "Now", "value": _fmt(result.price, result.currency), "inline": True}]
    if watch.get("target_price") is not None:
        fields.append({"name": "Target", "value": _fmt(watch["target_price"], result.currency), "inline": True})
    if item.get("lowest_price") is not None:
        fields.append({"name": "Lowest seen", "value": _fmt(item["lowest_price"], result.currency), "inline": True})

    why = []
    if "target" in reasons:
        why.append("hit your target price")
    if "low" in reasons:
        why.append("new lowest price")

    link = result.url or (watch["identifier"] if watch.get("source") == "url" else None)
    embed = {"title": f"📉 Price drop: {watch.get('label', 'item')}", "color": _GREEN, "fields": fields}
    if result.title:
        embed["description"] = result.title
    if link and str(link).startswith(("http://", "https://")):
        embed["url"] = link
    if why:
        embed["footer"] = {"text": "Alert: " + " · ".join(why)}
    return {"embeds": [embed]}


def build_summary_payload(outcomes: list) -> dict:
    """Build one Discord embed summarizing the current price of every item.

    ``outcomes`` is the list returned by :func:`run`. Items that dropped this
    run are flagged with 📉; items that couldn't be read show their error.
    """
    lines = []
    for watch, result, reasons in outcomes:
        label = watch.get("label", "item")
        if result.ok and result.price is not None:
            tag = " 📉" if reasons else ""
            lines.append(f"**{label}** — {_fmt(result.price, result.currency)}{tag}")
        else:
            lines.append(f"**{label}** — ⚠️ {result.error}")
    embed = {
        "title": "🛰️ PriceWatch check",
        "description": "\n".join(lines) if lines else "No items configured.",
        "color": _BLURPLE,
    }
    return {"embeds": [embed]}


async def run(watches: list[dict], state: dict, post_alert) -> list:
    """Core loop (no file/network I/O of its own — easy to test).

    Mutates ``state`` in place and awaits ``post_alert(watch, result, item, reasons)``
    for each drop. Returns a list of ``(watch, result, reasons)`` for every item
    (``reasons`` is empty when there's no drop / the price couldn't be read).
    """
    outcomes = []
    for index, watch in enumerate(watches):
        if index:
            await asyncio.sleep(INTER_ITEM_DELAY)
        fetch = sources.resolve(watch.get("source", ""))
        if fetch is None:
            result = sources.PriceResult(ok=False, error=f"unknown source {watch.get('source')!r}")
            print(f"skip: {result.error} for {watch.get('label')!r}")
            outcomes.append((watch, result, []))
            continue
        try:
            result = await fetch(watch["identifier"])
        except Exception as exc:  # noqa: BLE001
            result = sources.PriceResult(ok=False, error=str(exc))
            print(f"error: {watch.get('label')!r}: {exc}")
            outcomes.append((watch, result, []))
            continue
        if not result.ok or result.price is None:
            print(f"no price: {watch.get('label')!r} - {result.error}")
            outcomes.append((watch, result, []))
            continue

        key = _key(watch)
        stored = state.get(key) or {"last_price": None, "lowest_price": None, "alerted_target": False}
        item = {"target_price": watch.get("target_price"), **stored}
        reasons = alerts.evaluate(item, result.price)
        state[key] = {
            "last_price": item["last_price"],
            "lowest_price": item["lowest_price"],
            "alerted_target": item.get("alerted_target", False),
        }
        flag = f"  -> ALERT ({', '.join(reasons)})" if reasons else ""
        print(f"{watch.get('label')!r}: {_fmt(result.price, result.currency)}{flag}")
        outcomes.append((watch, result, reasons))
        if reasons:
            await post_alert(watch, result, item, reasons)

    # Drop state for watches that were removed from watches.json.
    valid = {_key(w) for w in watches}
    for key in [k for k in state if k not in valid]:
        del state[key]
    return outcomes


async def main() -> int:
    if not WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1
    watches = _load_json(WATCHES_PATH, [])
    if not watches:
        print(f"No watches configured in {WATCHES_PATH} — nothing to do.")
        return 0
    state = _load_json(STATE_PATH, {})

    async with aiohttp.ClientSession(timeout=sources.REQUEST_TIMEOUT) as session:
        async def post(payload):
            async with session.post(WEBHOOK_URL, json=payload) as resp:
                if resp.status >= 400:
                    print(f"webhook POST failed ({resp.status}): {await resp.text()}", file=sys.stderr)

        # In always-notify mode the per-run summary covers everything, so we
        # suppress the individual drop pings (the summary flags drops with 📉).
        async def on_drop(watch, result, item, reasons):
            if not ALWAYS_NOTIFY:
                await post(build_payload(watch, result, item, reasons))

        outcomes = await run(watches, state, on_drop)
        if ALWAYS_NOTIFY:
            await post(build_summary_payload(outcomes))

    _save_json(STATE_PATH, state)
    drops = sum(1 for _, _, reasons in outcomes if reasons)
    summary = " + summary" if ALWAYS_NOTIFY else ""
    print(f"Done — checked {len(watches)} item(s), {drops} drop(s){summary}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
