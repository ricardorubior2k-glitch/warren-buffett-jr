"""Financial Modeling Prep (FMP) provider — /stable/ API.

Wraps the current FMP `/stable/` REST API (the legacy `/api/v3/` endpoints
were retired): company profile, financial statements (income/balance/cash
flow, annual + quarterly), split/dividend-adjusted daily EOD prices, peers,
analyst estimates, insider trades (Form 4), institutional holders (13F),
and the earnings calendar. All `/stable/` endpoints take `symbol` as a
query parameter (not a path segment).

`FMPProvider` is disabled (`available == False`) when no API key is
configured; every public method then returns `None` immediately without
touching the cache or the network. Requests and caching are delegated to
`wbj.providers.base.Provider.get_json` — this module only builds
URLs/params and picks cache keys / max_age_days per data type. Endpoints
not included in the caller's plan return a non-JSON "Restricted Endpoint"
body, which `get_json` turns into `None` (graceful degradation).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from wbj.providers.base import Provider

BASE_URL = "https://financialmodelingprep.com/stable"

# max_age_days per cache key:
#   ohlcv_daily/quote 1, analyst_estimates 7, statements 30,
#   profile/peers/holders/insiders 7.
_MAX_AGE_OHLCV = 1
_MAX_AGE_ESTIMATES = 7
_MAX_AGE_STATEMENT = 30
_MAX_AGE_REFERENCE = 7
# Market-wide (delayed) data refreshes intraday: ~30 minutes.
_MAX_AGE_MARKET = 0.02
# Macro (treasury curve, indicators) moves daily.
_MAX_AGE_MACRO = 1
# Market-wide data is not per-ticker; it shares one cache namespace.
_MARKET_NS = "_market"


def _years_ago(d: date, years: int) -> date:
    """Return the date `years` years before `d`, handling Feb 29 safely."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year - years)


class FMPProvider(Provider):
    """Financial Modeling Prep data provider (/stable/ API)."""

    @property
    def available(self) -> bool:
        """True iff an FMP API key is configured."""
        return bool(self.settings and getattr(self.settings, "fmp_api_key", None))

    def _params(self, **extra: Any) -> dict[str, Any]:
        params = {"apikey": self.settings.fmp_api_key}
        params.update(extra)
        return params

    def profile(self, t: str) -> list | dict | None:
        """Company profile: name, sector, industry, market cap, price, beta."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/profile",
            self._params(symbol=t),
            "profile", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    def income_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual income statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/income-statement",
            self._params(symbol=t, period="annual", limit=limit),
            "income_annual", t, max_age_days=_MAX_AGE_STATEMENT,
        )

    def income_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly income statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/income-statement",
            self._params(symbol=t, period="quarter", limit=limit),
            "income_quarterly", t, max_age_days=_MAX_AGE_STATEMENT,
        )

    def balance_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual balance sheet statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/balance-sheet-statement",
            self._params(symbol=t, period="annual", limit=limit),
            "balance_annual", t, max_age_days=_MAX_AGE_STATEMENT,
        )

    def balance_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly balance sheet statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/balance-sheet-statement",
            self._params(symbol=t, period="quarter", limit=limit),
            "balance_quarterly", t, max_age_days=_MAX_AGE_STATEMENT,
        )

    def cashflow_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual cash flow statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/cash-flow-statement",
            self._params(symbol=t, period="annual", limit=limit),
            "cashflow_annual", t, max_age_days=_MAX_AGE_STATEMENT,
        )

    def cashflow_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly cash flow statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/cash-flow-statement",
            self._params(symbol=t, period="quarter", limit=limit),
            "cashflow_quarterly", t, max_age_days=_MAX_AGE_STATEMENT,
        )

    def ohlcv_daily(
        self, t: str, years: int = 3, today: date | None = None
    ) -> list | None:
        """Split/dividend-adjusted daily EOD bars for the past `years` years.

        `/stable/historical-price-eod/full` returns a flat list of
        `{symbol, date, open, high, low, close, volume, ...}` (newest
        first). `today` anchors the window and must be supplied by the
        caller so this stays deterministic under test.
        """
        if not self.available:
            return None
        if today is None:
            today = date.today()
        from_date = _years_ago(today, years)
        payload = self.get_json(
            f"{BASE_URL}/historical-price-eod/full",
            self._params(symbol=t, **{"from": from_date.isoformat(), "to": today.isoformat()}),
            "ohlcv_daily", t, max_age_days=_MAX_AGE_OHLCV,
        )
        if isinstance(payload, list):
            return payload
        # Some plans wrap the series; tolerate both shapes.
        if isinstance(payload, dict):
            return payload.get("historical")
        return None

    def peers(self, t: str) -> list | dict | None:
        """Peer tickers for `t`."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/stock-peers",
            self._params(symbol=t),
            "peers", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    def analyst_estimates(self, t: str, limit: int = 10) -> list | dict | None:
        """Analyst revenue/EPS estimates (annual)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/analyst-estimates",
            self._params(symbol=t, period="annual", limit=limit),
            "analyst_estimates", t, max_age_days=_MAX_AGE_ESTIMATES,
        )

    def insider_trades(self, t: str) -> list | dict | None:
        """SEC Form 4 insider trades, most recent 200."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/insider-trading/search",
            self._params(symbol=t, limit=200),
            "insider_trades", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    def institutional_holders(self, t: str) -> list | dict | None:
        """13F institutional holders (may be plan-restricted → None)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/institutional-ownership/extract-analytics/holder",
            self._params(symbol=t),
            "institutional_holders", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    def earnings_calendar(self, t: str) -> list | dict | None:
        """Earnings calendar (actual vs. estimated EPS/revenue)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/earnings",
            self._params(symbol=t, limit=40),
            "earnings_calendar", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    # --- market-wide (delayed) feeds --------------------------------------
    # These power the terminal's market panels. They are not per-ticker, so
    # they share the `_market` cache namespace and refresh intraday.

    def biggest_gainers(self) -> list | dict | None:
        """Day's biggest gainers across the market (delayed)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/biggest-gainers", self._params(),
            "biggest_gainers", _MARKET_NS, max_age_days=_MAX_AGE_MARKET,
        )

    def biggest_losers(self) -> list | dict | None:
        """Day's biggest losers across the market (delayed)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/biggest-losers", self._params(),
            "biggest_losers", _MARKET_NS, max_age_days=_MAX_AGE_MARKET,
        )

    def most_actives(self) -> list | dict | None:
        """Day's most active names by volume (delayed)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/most-actives", self._params(),
            "most_actives", _MARKET_NS, max_age_days=_MAX_AGE_MARKET,
        )

    def quote(self, symbol: str) -> list | dict | None:
        """Single-symbol quote (works for stocks and indexes, e.g. ^GSPC).

        Batch-quote is plan-restricted on lower tiers, so callers that need
        several symbols call this once per symbol.
        """
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/quote", self._params(symbol=symbol),
            f"quote_{symbol}", _MARKET_NS, max_age_days=_MAX_AGE_MARKET,
        )

    def sector_snapshot(self, day: date) -> list | dict | None:
        """Sector performance snapshot (average % change) for one date."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/sector-performance-snapshot",
            self._params(date=day.isoformat()),
            f"sector_{day.isoformat()}", _MARKET_NS, max_age_days=_MAX_AGE_MACRO,
        )

    def treasury_rates(self) -> list | dict | None:
        """US Treasury yield curve, recent history (newest first)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/treasury-rates", self._params(),
            "treasury_rates", _MARKET_NS, max_age_days=_MAX_AGE_MACRO,
        )

    def economic_indicator(self, name: str) -> list | dict | None:
        """A named macro indicator series (e.g. GDP, CPI)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/economic-indicators", self._params(name=name),
            f"econ_{name}", _MARKET_NS, max_age_days=_MAX_AGE_MACRO,
        )
