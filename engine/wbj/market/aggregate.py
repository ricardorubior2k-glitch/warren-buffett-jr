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


# Curated liquid universe for FinnHub-computed movers. FinnHub has no
# market-wide gainers/losers endpoint, so we quote these names live and rank by
# % change — used when FMP's market-wide movers is rate-limited/plan-restricted.
MOVERS_UNIVERSE = (
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "NFLX",
    "AVGO", "JPM", "XOM", "WMT", "COST", "DIS", "INTC", "CRM", "ORCL", "ADBE",
    "PLTR", "COIN", "UBER", "MU", "QCOM",
)


def movers_finnhub(
    finnhub, universe: tuple[str, ...] = MOVERS_UNIVERSE,
    limit: int = 8, now: datetime | None = None,
) -> dict:
    """Top movers computed LIVE from FinnHub quotes over a curated universe.

    FinnHub exposes no market-wide movers feed, so we quote a liquid set and
    rank by % change. Returns the same gainers/losers/actives contract as
    `movers`, plus `source` and an ISO `as_of` timestamp so the terminal can
    label the panel as live (vs. FMP's cached fallback).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for sym in universe:
        q = finnhub.realtime_quote(sym)
        if not (isinstance(q, dict) and q.get("c") and q.get("pc")):
            continue
        c, pc = float(q["c"]), float(q["pc"])
        rows.append({
            "symbol": sym,
            "name": None,
            "price": _num(c),
            "change": _num(c - pc),
            "change_pct": _num(q.get("dp")),
        })
    rows = [r for r in rows if r["change_pct"] is not None]
    gainers = sorted(rows, key=lambda r: r["change_pct"], reverse=True)[:limit]
    losers = sorted(rows, key=lambda r: r["change_pct"])[:limit]
    actives = sorted(rows, key=lambda r: abs(r["change_pct"] or 0), reverse=True)[:limit]
    return {
        "gainers": gainers,
        "losers": losers,
        "actives": actives,
        "source": "finnhub",
        "as_of": now.isoformat(),
        "live": True,
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


def stream_event(finnhub, symbols: list[str], seq: int,
                 now: datetime | None = None) -> dict:
    """Build one live-stream tick: sequence, server time, and fresh quotes.

    FinnHub's quote payload is `{c: current, dp: change%, pc: prev close, ...}`.
    Symbols whose quote is unavailable (or has no price) are simply omitted —
    the tick still carries its seq/timestamp so the client sees a live heartbeat.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    quotes = []
    for sym in symbols:
        q = finnhub.realtime_quote(sym)
        if isinstance(q, dict) and q.get("c"):
            quotes.append({
                "symbol": sym.upper(),
                "price": _num(q.get("c")),
                "change_pct": _num(q.get("dp")),
                "prev_close": _num(q.get("pc")),
            })
    return {"seq": seq, "ts": now.isoformat(), "quotes": quotes}
