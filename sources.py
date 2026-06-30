"""Price sources for PriceWatch.

Each source is an ``async`` function that takes an identifier (a Best Buy SKU,
a product URL, ...) and returns a :class:`PriceResult`. Register new sources in
:data:`SOURCES` / :func:`resolve` and they become available to ``/watch``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterator, Optional

import aiohttp
from bs4 import BeautifulSoup

# A real-ish desktop UA. Some sites 403 obvious bots; this is best-effort and
# does not defeat determined anti-scraping (see README caveats).
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# A fuller, browser-like header set. Helps with sites that do basic header
# checks; it will NOT defeat serious bot protection (Akamai/PerimeterX), which
# big retailers use — those tend to block by IP reputation regardless.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Retry transient blocks/timeouts — big retailers intermittently 403 automated
# requests even from a residential IP, especially under a burst of requests.
MAX_FETCH_ATTEMPTS = 3
RETRY_BACKOFF = (3, 6)  # seconds to wait before the 2nd and 3rd attempts


@dataclass
class PriceResult:
    """The outcome of a single price lookup."""

    ok: bool
    price: Optional[float] = None
    currency: str = "USD"
    title: Optional[str] = None
    url: Optional[str] = None
    in_stock: Optional[bool] = None
    error: Optional[str] = None


# --------------------------------------------------------------------------- #
# Best Buy (official Products API)
# --------------------------------------------------------------------------- #

BESTBUY_ENDPOINT = "https://api.bestbuy.com/v1/products/{sku}.json"


async def fetch_bestbuy(identifier: str) -> PriceResult:
    """Look up a Best Buy SKU via the official Products API.

    Register a free key at developer.bestbuy.com and put it in ``BESTBUY_API_KEY``.
    """
    api_key = os.getenv("BESTBUY_API_KEY")
    if not api_key:
        return PriceResult(ok=False, error="BESTBUY_API_KEY is not set")

    sku = identifier.strip()
    if not sku.isdigit():
        # Be forgiving: pull the SKU out of a pasted Best Buy URL.
        match = re.search(r"(\d{6,})", sku)
        if not match:
            return PriceResult(ok=False, error=f"Not a valid Best Buy SKU: {identifier!r}")
        sku = match.group(1)

    params = {
        "apiKey": api_key,
        "show": "sku,name,salePrice,regularPrice,onlineAvailability,url",
        "format": "json",
    }
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(
                BESTBUY_ENDPOINT.format(sku=sku),
                params=params,
                headers={"User-Agent": USER_AGENT},
            ) as resp:
                if resp.status == 403:
                    return PriceResult(ok=False, error="Best Buy API rejected the key (403)")
                if resp.status == 404:
                    return PriceResult(ok=False, error=f"SKU {sku} not found")
                resp.raise_for_status()
                data = await resp.json()
    except asyncio.TimeoutError:
        return PriceResult(ok=False, error="Best Buy API request timed out")
    except aiohttp.ClientError as exc:
        return PriceResult(ok=False, error=f"Network error: {exc}")

    price = data.get("salePrice")
    if price is None:
        return PriceResult(ok=False, error="Best Buy returned no salePrice")
    return PriceResult(
        ok=True,
        price=float(price),
        currency="USD",
        title=data.get("name"),
        url=data.get("url"),
        in_stock=data.get("onlineAvailability"),
    )


# --------------------------------------------------------------------------- #
# Generic product page (schema.org JSON-LD or price meta tags)
# --------------------------------------------------------------------------- #


async def fetch_url(identifier: str) -> PriceResult:
    """Scrape a generic product page for structured price data.

    Retries on a 403 / timeout: big retailers intermittently block automated
    requests even from a residential IP, and a short wait usually clears it.
    """
    url = identifier.strip()
    if not url.startswith(("http://", "https://")):
        return PriceResult(ok=False, error=f"Not a valid URL: {identifier!r}")

    last = PriceResult(ok=False, url=url, error="request failed")
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        for attempt in range(MAX_FETCH_ATTEMPTS):
            if attempt:
                await asyncio.sleep(RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)])
            try:
                async with session.get(url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 403:
                        last = PriceResult(ok=False, url=url, error="Site blocked the request (403)")
                        continue  # transient — retry after a pause
                    resp.raise_for_status()
                    html = await resp.text()
                return parse_product_html(html, url)
            except asyncio.TimeoutError:
                last = PriceResult(ok=False, url=url, error="Request timed out")
            except aiohttp.ClientError as exc:
                last = PriceResult(ok=False, url=url, error=f"Network error: {exc}")
    return last


def parse_product_html(html: str, url: str) -> PriceResult:
    """Extract a price from page HTML (pure function — handy for tests)."""
    soup = BeautifulSoup(html, "html.parser")
    return (
        _from_jsonld(soup, url)
        or _from_meta(soup, url)
        or PriceResult(ok=False, url=url, error="No structured price data found on the page")
    )


def _coerce_price(value) -> Optional[float]:
    """Turn ``"$1,299.00"`` / ``1299`` / ``"1299.0"`` into a float, or ``None``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _flatten(data) -> Iterator[dict]:
    """Yield every dict in a JSON-LD blob, descending into lists and ``@graph``."""
    if isinstance(data, list):
        for item in data:
            yield from _flatten(item)
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _flatten(item)
        yield data


def _is_product(obj: dict) -> bool:
    type_ = obj.get("@type")
    if isinstance(type_, list):
        return any("Product" in str(t) for t in type_)
    return isinstance(type_, str) and "Product" in type_


def _extract_offer(offers):
    """Return ``(price, currency, in_stock)`` from a schema.org ``offers`` value."""
    if isinstance(offers, list):
        for offer in offers:
            price, currency, in_stock = _extract_offer(offer)
            if price is not None:
                return price, currency, in_stock
        return None, None, None
    if not isinstance(offers, dict):
        return None, None, None

    price = _coerce_price(offers.get("price"))
    currency = offers.get("priceCurrency")
    if price is None:
        spec = offers.get("priceSpecification")
        if isinstance(spec, list):
            spec = spec[0] if spec else None
        if isinstance(spec, dict):
            price = _coerce_price(spec.get("price"))
            currency = currency or spec.get("priceCurrency")

    availability = offers.get("availability")
    in_stock = "instock" in availability.lower().replace("_", "") if isinstance(availability, str) else None
    return price, currency, in_stock


def _from_jsonld(soup: BeautifulSoup, url: str) -> Optional[PriceResult]:
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _flatten(data):
            if not _is_product(obj):
                continue
            price, currency, in_stock = _extract_offer(obj.get("offers"))
            if price is None:
                continue
            return PriceResult(
                ok=True,
                price=price,
                currency=currency or "USD",
                title=obj.get("name"),
                url=obj.get("url") or url,
                in_stock=in_stock,
            )
    return None


def _from_meta(soup: BeautifulSoup, url: str) -> Optional[PriceResult]:
    price = None
    for attrs in (
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
        {"name": "price"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag:
            price = _coerce_price(tag.get("content") or tag.get("value"))
            if price is not None:
                break
    if price is None:
        return None

    currency = None
    for attrs in (
        {"property": "product:price:currency"},
        {"property": "og:price:currency"},
        {"itemprop": "priceCurrency"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and (tag.get("content") or tag.get("value")):
            currency = tag.get("content") or tag.get("value")
            break

    title_tag = soup.find("meta", attrs={"property": "og:title"}) or soup.find("title")
    title = None
    if title_tag is not None:
        title = title_tag.get("content") if title_tag.has_attr("content") else title_tag.get_text(strip=True)

    return PriceResult(ok=True, price=price, currency=currency or "USD", title=title, url=url)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

SOURCES: dict[str, Callable[[str], Awaitable[PriceResult]]] = {
    "bestbuy": fetch_bestbuy,
    "url": fetch_url,
    # "keepa": fetch_keepa,   # <- add an Amazon source here (see README)
}


def resolve(source: str) -> Optional[Callable[[str], Awaitable[PriceResult]]]:
    """Return the fetch function for a source key, or ``None`` if unknown."""
    return SOURCES.get((source or "").strip().lower())
