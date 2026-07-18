"""Company search ranking for the web app.

Ranks SEC `company_tickers` entries against a query so that typing a company
*name* surfaces the companies whose name starts with those letters — not only
ticker-symbol matches. Results fall into four priority tiers:

    1. exact ticker      (``AAPL`` -> AAPL)
    2. ticker prefix      (``APP`` -> APP, APPS, ...)     sorted by ticker
    3. name prefix        (``MICRO`` -> Micron, Microsoft) sorted by name
    4. name contains      (``SOLAR`` -> First Solar)       sorted by name

The name-prefix tier is the fix for "typing a name should list the companies
that start with those letters"; the contains tier is kept as a fallback. Each
entry lands in the single highest tier it qualifies for.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LIMIT = 15


def rank(entries: list[dict], q: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Return `{ticker, name}` rows for `q`, tier-ranked and capped at `limit`."""
    q = (q or "").strip().upper()
    if not q:
        return []

    exact_t: list[dict] = []
    prefix_t: list[dict] = []
    prefix_n: list[dict] = []
    contains_n: list[dict] = []

    for e in entries:
        if not isinstance(e, dict):
            continue
        ticker = str(e.get("ticker", "")).upper()
        if not ticker:
            continue
        name_up = str(e.get("title", "")).upper()
        row: dict[str, Any] = {"ticker": ticker, "name": e.get("title", "")}

        if ticker == q:
            exact_t.append(row)
        elif ticker.startswith(q):
            prefix_t.append(row)
        elif name_up.startswith(q):
            prefix_n.append(row)
        elif q in name_up:
            contains_n.append(row)

    prefix_t.sort(key=lambda r: r["ticker"])
    prefix_n.sort(key=lambda r: str(r["name"]).upper())
    contains_n.sort(key=lambda r: str(r["name"]).upper())

    return (exact_t + prefix_t + prefix_n + contains_n)[:limit]
