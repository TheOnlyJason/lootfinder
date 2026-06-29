#!/usr/bin/env python3
"""Quick check: can the Generic URL scraper read a price from a page?

    python check_url.py <product-url>

Run this from the machine that will host the bot. Whether a retailer blocks
automated requests depends on your IP address, so a page that fails on a cloud
server may work fine from your home network (and vice-versa). Use it to find
which stores work for *you* before adding them with /watch.
"""

from __future__ import annotations

import asyncio
import sys

import sources


async def _check(url: str) -> int:
    print(f"Fetching {url} ...\n")
    result = await sources.fetch_url(url)
    if result.ok:
        print("✅ Found a price the bot can track:")
        print(f"   price:    {result.price} {result.currency}")
        print(f"   title:    {result.title or '—'}")
        print(f"   in stock: {result.in_stock}")
        print("\nAdd it in Discord with:")
        print(f"   /watch  source: Generic URL  identifier: {url}  label: <name>  [target_price: <n>]")
        return 0
    print(f"❌ No usable price — {result.error}")
    print(
        "\nThis store either blocks automated requests from your network, or doesn't\n"
        "expose structured (schema.org / meta-tag) price data. Try a different retailer —\n"
        "smaller / Shopify-based stores tend to work best."
    )
    return 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python check_url.py <product-url>")
        return 2
    return asyncio.run(_check(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
