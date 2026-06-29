"""Watchlist persistence.

The watchlist is a flat JSON array of item dicts stored in ``watchlist.json``
(override with ``WATCHLIST_PATH``). Writes are atomic so a crash mid-save can't
corrupt the file.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading

WATCHLIST_PATH = os.getenv("WATCHLIST_PATH", "watchlist.json")
_write_lock = threading.Lock()


def load() -> list[dict]:
    """Return the watchlist, or ``[]`` if it's missing or unreadable."""
    if not os.path.exists(WATCHLIST_PATH):
        return []
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def save(items: list[dict]) -> None:
    """Atomically write the watchlist to disk."""
    with _write_lock:
        directory = os.path.dirname(os.path.abspath(WATCHLIST_PATH))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(items, fh, indent=2)
            os.replace(tmp, WATCHLIST_PATH)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
