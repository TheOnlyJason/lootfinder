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

import alerts
import sources

WATCHES_PATH = os.getenv("WATCHES_PATH", "watches.json")
STATE_PATH = os.getenv("STATE_PATH", "state.json")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

_CURRENCY_SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "C$", "AUD": "A$"}
_GREEN = 0x2ECC71


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


async def run(watches: list[dict], state: dict, post_alert) -> int:
    """Core loop (no file/network I/O of its own — easy to test).

    Mutates ``state`` in place and awaits ``post_alert(watch, result, item, reasons)``
    for each drop. Returns the number of alerts posted.
    """
    posted = 0
    for watch in watches:
        fetch = sources.resolve(watch.get("source", ""))
        if fetch is None:
            print(f"skip: unknown source {watch.get('source')!r} for {watch.get('label')!r}")
            continue
        try:
            result = await fetch(watch["identifier"])
        except Exception as exc:  # noqa: BLE001
            print(f"error: {watch.get('label')!r}: {exc}")
            continue
        if not result.ok or result.price is None:
            print(f"no price: {watch.get('label')!r} - {result.error}")
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
        if reasons:
            await post_alert(watch, result, item, reasons)
            posted += 1

    # Drop state for watches that were removed from watches.json.
    valid = {_key(w) for w in watches}
    for key in [k for k in state if k not in valid]:
        del state[key]
    return posted


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
        async def post_alert(watch, result, item, reasons):
            payload = build_payload(watch, result, item, reasons)
            async with session.post(WEBHOOK_URL, json=payload) as resp:
                if resp.status >= 400:
                    print(f"webhook POST failed ({resp.status}): {await resp.text()}", file=sys.stderr)

        posted = await run(watches, state, post_alert)

    _save_json(STATE_PATH, state)
    print(f"Done — checked {len(watches)} item(s), posted {posted} alert(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
