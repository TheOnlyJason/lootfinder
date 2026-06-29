"""Alert de-duplication logic.

A watchlist item carries three pieces of price state:

* ``last_price``      — the price seen on the most recent check
* ``lowest_price``    — the lowest price ever seen (``None`` before the first check)
* ``alerted_target``  — whether we've already fired the "hit target" alert

:func:`evaluate` updates that state in place and decides whether this check is
worth pinging the channel about. We alert only when the price drops to a **new
low**, or when it **first crosses the target** — never repeatedly for a price
the user has already been told about.
"""

from __future__ import annotations


def evaluate(item: dict, current: float) -> list[str]:
    """Update ``item``'s price state and return the alert reasons for ``current``.

    Reasons are a subset of ``{"target", "low"}``; an empty list means "no alert".
    """
    reasons: list[str] = []
    target = item.get("target_price")
    lowest = item.get("lowest_price")
    first_check = lowest is None

    # "First crosses your target." We re-arm once the price climbs back above the
    # target, so a later dip can alert again.
    if target is not None:
        if current <= target and not item.get("alerted_target"):
            reasons.append("target")
            item["alerted_target"] = True
        elif current > target:
            item["alerted_target"] = False

    # "Drops to a new low." Skipped on the very first observation — there's no
    # prior price to have dropped from, so adding an item never self-alerts.
    if not first_check and current < lowest:
        reasons.append("low")

    if first_check or current < lowest:
        item["lowest_price"] = current
    item["last_price"] = current
    return reasons
