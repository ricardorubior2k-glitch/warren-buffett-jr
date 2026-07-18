"""Market-wide aggregation for the WBJ Terminal panels (Phase 1).

Pure functions that take an `FMPProvider` and return clean, minimal dicts
matching the terminal's frontend contract. Each raw FMP shape is normalized
here so the frontend never sees provider-specific field names.

Every function degrades gracefully: if the provider is unavailable or an
endpoint is plan-restricted (returns None), the function returns an empty
but well-formed structure rather than raising. This mirrors the rest of the
engine's "missing data is never an error, just absent" contract.

News is intentionally absent: FMP's news endpoints are plan-restricted on
lower tiers (HTTP 402). A FinnHub-backed `news()` is a separate follow-up.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

# How many prior calendar days to walk back looking for a sector snapshot
# (weekends/holidays have no EOD row).
_SECTOR_LOOKBACK_DAYS = 6

# Company-news lookback window.
_NEWS_LOOKBACK_DAYS = 14

# Yield-curve tenors in the FMP treasury-rates row, in maturity order.
_TENORS = [
    ("month1", "1M"), ("month2", "2M"), ("month3", "3M"), ("month6", "6M"),
    ("year1", "1Y"), ("year2", "2Y"), ("year3", "3Y"), ("year5", "5Y"),
    ("year7", "7Y"), ("year10", "10Y"), ("year20", "20Y"), ("year30", "30Y"),
]


def _as_list(payload: Any) -> list[dict]:
    """Coerce a provider payload to a list of dicts (empty on anything else)."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def _num(v: Any) -> float | None:
    """Best-effort float; None for missing/garbage (never raises)."""
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _clean_mover(row: dict) -> dict:
    """Normalize one gainer/loser/active row to the frontend contract."""
    # Gainers/losers use 'changesPercentage'; quotes use 'changePercentage'.
    pct = row.get("changesPercentage", row.get("changePercentage"))
    return {
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "price": _num(row.get("price")),
        "change": _num(row.get("change")),
        "change_pct": _num(pct),
    }


def movers(fmp, limit: int = 8) -> dict:
    """Top gainers / losers / most-actives, trimmed to `limit` each."""
    def top(payload):
        rows = [_clean_mover(r) for r in _as_list(payload)]
        return [r for r in rows if r["symbol"]][:limit]

    return {
        "gainers": top(fmp.biggest_gainers()),
        "losers": top(fmp.biggest_losers()),
        "actives": top(fmp.most_actives()),
    }


def heatmap(fmp, today: date | None = None) -> dict:
    """Sector performance heatmap (average % change per sector).

    Walks back from `today` up to a week to find the most recent snapshot
    with data (skips weekends/holidays). Returns {date, sectors:[...]}.
    """
    if today is None:
        today = date.today()
    for back in range(_SECTOR_LOOKBACK_DAYS + 1):
        day = today - timedelta(days=back)
        rows = _as_list(fmp.sector_snapshot(day))
        if rows:
            sectors = [
                {"sector": r.get("sector"), "change_pct": _num(r.get("averageChange"))}
                for r in rows
                if r.get("sector")
            ]
            sectors.sort(key=lambda s: (s["change_pct"] is None, -(s["change_pct"] or 0)))
            return {"date": day.isoformat(), "sectors": sectors}
    return {"date": None, "sectors": []}


def _latest_curve(rows: list[dict]) -> dict:
    """Pick the newest treasury row and shape it into an ordered curve."""
    if not rows:
        return {"date": None, "curve": []}
    latest = max(rows, key=lambda r: r.get("date", ""))
    curve = []
    for field, label in _TENORS:
        rate = _num(latest.get(field))
        if rate is not None:
            curve.append({"tenor": label, "rate": rate})
    return {"date": latest.get("date"), "curve": curve}


def macro(fmp, indicators: tuple[str, ...] = ("GDP",)) -> dict:
    """Treasury yield curve plus a few named macro indicators."""
    curve = _latest_curve(_as_list(fmp.treasury_rates()))
    out_ind: dict[str, dict] = {}
    for name in indicators:
        rows = _as_list(fmp.economic_indicator(name))
        if rows:
            latest = max(rows, key=lambda r: r.get("date", ""))
            out_ind[name] = {"date": latest.get("date"), "value": _num(latest.get("value"))}
    return {
        "curve_date": curve["date"],
        "curve": curve["curve"],
        "indicators": out_ind,
    }


def _iso_ts(unix_seconds: Any) -> str | None:
    """FinnHub gives article time as a unix timestamp; return an ISO string."""
    try:
        return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _clean_article(row: dict) -> dict:
    """Normalize one FinnHub article to the frontend contract."""
    return {
        "headline": row.get("headline"),
        "source": row.get("source"),
        "url": row.get("url"),
        "datetime": _iso_ts(row.get("datetime")),
        "ts": row.get("datetime") if isinstance(row.get("datetime"), (int, float)) else 0,
        "related": row.get("related") or None,
    }


def _news_rows(payload: Any, limit: int) -> list[dict]:
    """Clean, drop headline-less rows, sort newest-first, trim to `limit`."""
    rows = [_clean_article(r) for r in _as_list(payload)]
    rows = [r for r in rows if r["headline"] and r["url"]]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    for r in rows:
        r.pop("ts", None)
    return rows[:limit]


def market_news(finnhub, limit: int = 12) -> dict:
    """Latest general market news (FinnHub — FMP's news is plan-restricted)."""
    return {"articles": _news_rows(finnhub.general_news(), limit)}


def company_news(finnhub, ticker: str, limit: int = 6,
                 today: date | None = None) -> dict:
    """Recent company-specific news for `ticker` (last two weeks)."""
    if today is None:
        today = date.today()
    frm = today - timedelta(days=_NEWS_LOOKBACK_DAYS)
    return {
        "ticker": ticker.upper(),
        "articles": _news_rows(finnhub.company_news(ticker, frm, today), limit),
    }
