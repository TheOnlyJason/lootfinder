"""Offline tests — no Discord token, no network.

Run with:  python -m pytest tests/   (or just  python tests/test_offline.py)

Covers the two parts that are easy to get subtly wrong: HTML price parsing and
the new-low / target-crossing de-dupe logic.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402
import check_once  # noqa: E402
import sources  # noqa: E402

JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Mac Studio",
 "offers":{"@type":"Offer","price":"1,999.00","priceCurrency":"USD","availability":"https://schema.org/InStock"}}
</script>
</head><body>Mac Studio</body></html>
"""

GRAPH_PAGE = """
<html><head>
<script type="application/ld+json">
{"@graph":[{"@type":"WebPage"},
 {"@type":["Product","Thing"],"name":"Widget",
  "offers":[{"@type":"Offer","priceSpecification":{"price":42.5,"priceCurrency":"GBP"}}]}]}
</script>
</head></html>
"""

META_PAGE = """
<html><head>
<meta property="og:title" content="Cheap Thing">
<meta property="product:price:amount" content="19.99">
<meta property="product:price:currency" content="EUR">
</head></html>
"""

NO_PRICE_PAGE = "<html><head><title>Nothing here</title></head><body>hi</body></html>"


def test_jsonld_price():
    r = sources.parse_product_html(JSONLD_PAGE, "https://x/mac-studio")
    assert r.ok and r.price == 1999.0 and r.currency == "USD"
    assert r.title == "Mac Studio" and r.in_stock is True


def test_graph_and_price_specification():
    r = sources.parse_product_html(GRAPH_PAGE, "https://x/widget")
    assert r.ok and r.price == 42.5 and r.currency == "GBP"


def test_meta_fallback():
    r = sources.parse_product_html(META_PAGE, "https://x/thing")
    assert r.ok and r.price == 19.99 and r.currency == "EUR" and r.title == "Cheap Thing"


def test_no_structured_data():
    r = sources.parse_product_html(NO_PRICE_PAGE, "https://x/none")
    assert not r.ok and r.error


def test_coerce_price():
    assert sources._coerce_price("$1,299.00") == 1299.0
    assert sources._coerce_price(50) == 50.0
    assert sources._coerce_price("free") is None
    assert sources._coerce_price(None) is None


def test_first_check_never_alerts_without_target():
    item = {"target_price": None, "lowest_price": None, "alerted_target": False}
    assert alerts.evaluate(item, 500.0) == []
    assert item["lowest_price"] == 500.0 and item["last_price"] == 500.0


def test_alerts_only_on_new_low():
    item = {"target_price": None, "lowest_price": None, "alerted_target": False}
    alerts.evaluate(item, 500.0)            # seed, no alert
    assert alerts.evaluate(item, 500.0) == []   # same price -> no alert
    assert alerts.evaluate(item, 450.0) == ["low"]   # new low -> alert
    assert alerts.evaluate(item, 460.0) == []   # higher than low -> no alert
    assert alerts.evaluate(item, 440.0) == ["low"]   # newer low -> alert


def test_target_crossing_fires_once_then_rearms():
    item = {"target_price": 450.0, "lowest_price": None, "alerted_target": False}
    alerts.evaluate(item, 500.0)            # seed above target, no alert
    # Dropping to target from a higher seed is both a target-cross and a new low.
    assert alerts.evaluate(item, 450.0) == ["target", "low"]
    assert alerts.evaluate(item, 450.0) == []           # still at target -> quiet
    assert alerts.evaluate(item, 500.0) == []           # back up -> re-arm, no alert
    assert alerts.evaluate(item, 449.0) == ["target", "low"]  # crosses again AND new low


def test_seeded_below_target_alerts_next_check():
    # /watch seeds last/lowest with the current price; if already below target,
    # the *next* check should fire the target alert exactly once.
    item = {"target_price": 450.0, "lowest_price": 400.0, "last_price": 400.0, "alerted_target": False}
    assert alerts.evaluate(item, 400.0) == ["target"]
    assert alerts.evaluate(item, 400.0) == []


def test_check_once_seeds_then_alerts_on_drop():
    price = {"v": 999.0}

    async def stub(identifier):
        return sources.PriceResult(ok=True, price=price["v"], currency="USD", title="T", url=identifier)

    sources.SOURCES["stub"] = stub
    try:
        watches = [{"source": "stub", "identifier": "x", "label": "X", "target_price": 899}]
        state = {}
        posts = []

        async def post(w, result, item, reasons):
            posts.append((result.price, list(reasons)))

        # First run seeds at 999 (above target) -> no alert.
        outcomes = asyncio.run(check_once.run(watches, state, post))
        assert len(outcomes) == 1 and posts == [] and state["stub:x"]["lowest_price"] == 999.0

        # Drop to a new low -> alert.
        price["v"] = 950.0
        asyncio.run(check_once.run(watches, state, post))
        assert posts[-1] == (950.0, ["low"])

        # Drop onto the target -> target + low.
        price["v"] = 899.0
        asyncio.run(check_once.run(watches, state, post))
        assert posts[-1] == (899.0, ["target", "low"])

        # Same price again -> quiet (de-duped).
        before = len(posts)
        asyncio.run(check_once.run(watches, state, post))
        assert len(posts) == before
    finally:
        sources.SOURCES.pop("stub", None)


def test_check_once_prunes_removed_watches():
    state = {"url:gone": {"last_price": 1, "lowest_price": 1, "alerted_target": False}}

    async def post(*_args):
        pass

    asyncio.run(check_once.run([], state, post))
    assert state == {}


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    _run_all()
