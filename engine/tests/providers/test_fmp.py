"""Tests for wbj.providers.fmp.FMPProvider."""

import json
from datetime import date
from pathlib import Path

import httpx

from wbj.config import Settings
from wbj.providers.cache import Cache
from wbj.providers.fmp import FMPProvider

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "fmp"


def _load_fixture(name: str):
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


def _make_provider(tmp_path, handler, fmp_api_key="testkey"):
    """Build an FMPProvider wired to a MockTransport-backed httpx.Client."""
    settings = Settings(fmp_api_key=fmp_api_key)
    cache = Cache(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FMPProvider(settings, cache, client=client)


def _capturing_handler(fixture_name, captured):
    """Return a handler that records the request and replies with a fixture."""

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, json=_load_fixture(fixture_name))

    return handler


# --- availability -----------------------------------------------------------


def test_available_true_when_api_key_set(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=[]))
    assert p.available is True


def test_available_false_when_api_key_missing(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=[]), fmp_api_key=None)
    assert p.available is False


def test_all_methods_return_none_and_skip_network_when_unavailable(tmp_path):
    def handler(request):
        raise AssertionError("transport should not be called when unavailable")

    p = _make_provider(tmp_path, handler, fmp_api_key=None)

    assert p.profile("NVDA") is None
    assert p.income_annual("NVDA") is None
    assert p.income_quarterly("NVDA") is None
    assert p.balance_annual("NVDA") is None
    assert p.balance_quarterly("NVDA") is None
    assert p.cashflow_annual("NVDA") is None
    assert p.cashflow_quarterly("NVDA") is None
    assert p.ohlcv_daily("NVDA", today=date(2026, 7, 16)) is None
    assert p.peers("NVDA") is None
    assert p.analyst_estimates("NVDA") is None
    assert p.insider_trades("NVDA") is None
    assert p.institutional_holders("NVDA") is None
    assert p.earnings_calendar("NVDA") is None


# --- profile ------------------------------------------------------------


def test_profile_url_params_and_payload(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("profile", captured))

    result = p.profile("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/profile"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("apikey") is not None
    assert result == _load_fixture("profile")


# --- income statements ----------------------------------------------------


def test_income_annual_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("income_annual", captured))

    result = p.income_annual("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/income-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "annual"
    assert req.url.params.get("limit") == "6"
    assert req.url.params.get("apikey") is not None
    assert result == _load_fixture("income_annual")


def test_income_annual_custom_limit(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("income_annual", captured))

    p.income_annual("NVDA", limit=3)

    assert captured["request"].url.params.get("limit") == "3"


def test_income_quarterly_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("income_quarterly", captured))

    result = p.income_quarterly("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/income-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "quarter"
    assert req.url.params.get("limit") == "21"
    assert result == _load_fixture("income_quarterly")


# --- balance sheet ----------------------------------------------------------


def test_balance_annual_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("balance_annual", captured))

    result = p.balance_annual("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/balance-sheet-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "annual"
    assert req.url.params.get("limit") == "6"
    assert result == _load_fixture("balance_annual")


def test_balance_quarterly_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("balance_quarterly", captured))

    result = p.balance_quarterly("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/balance-sheet-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "quarter"
    assert req.url.params.get("limit") == "21"
    assert result == _load_fixture("balance_quarterly")


# --- cash flow ---------------------------------------------------------------


def test_cashflow_annual_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("cashflow_annual", captured))

    result = p.cashflow_annual("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/cash-flow-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "annual"
    assert req.url.params.get("limit") == "6"
    assert result == _load_fixture("cashflow_annual")


def test_cashflow_quarterly_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("cashflow_quarterly", captured))

    result = p.cashflow_quarterly("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/cash-flow-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "quarter"
    assert req.url.params.get("limit") == "21"
    assert result == _load_fixture("cashflow_quarterly")


# --- OHLCV -------------------------------------------------------------------


def test_ohlcv_daily_url_params_and_returns_flat_list(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("ohlcv_daily", captured))

    result = p.ohlcv_daily("NVDA", years=3, today=date(2026, 7, 16))

    req = captured["request"]
    assert req.url.path == "/stable/historical-price-eod/full"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("from") == "2023-07-16"
    assert req.url.params.get("to") == "2026-07-16"
    # /stable/historical-price-eod/full returns a top-level JSON array (not
    # a {"historical": [...]} wrapper); the provider returns it as-is.
    assert result == _load_fixture("ohlcv_daily")


def test_ohlcv_daily_default_years_is_3(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("ohlcv_daily", captured))

    p.ohlcv_daily("NVDA", today=date(2026, 7, 16))

    assert captured["request"].url.params.get("from") == "2023-07-16"


def test_ohlcv_daily_missing_historical_key_returns_none(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"symbol": "NVDA"})

    p = _make_provider(tmp_path, handler)

    assert p.ohlcv_daily("NVDA", today=date(2026, 7, 16)) is None


def test_ohlcv_daily_tolerates_dict_wrapped_historical_shape(tmp_path):
    """Some plans may still wrap the series in {"historical": [...]}; the
    provider falls back to that shape when the payload isn't a flat list."""

    def handler(request):
        return httpx.Response(200, json={"symbol": "NVDA", "historical": _load_fixture("ohlcv_daily")})

    p = _make_provider(tmp_path, handler)

    result = p.ohlcv_daily("NVDA", today=date(2026, 7, 16))

    assert result == _load_fixture("ohlcv_daily")


# --- peers ---------------------------------------------------------------


def test_peers_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("peers", captured))

    result = p.peers("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/stock-peers"
    assert req.url.params.get("symbol") == "NVDA"
    assert result == _load_fixture("peers")


# --- analyst estimates ----------------------------------------------------


def test_analyst_estimates_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("analyst_estimates", captured))

    result = p.analyst_estimates("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/analyst-estimates"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "annual"
    assert req.url.params.get("limit") == "10"
    assert result == _load_fixture("analyst_estimates")


# --- insider trades --------------------------------------------------------


def test_insider_trades_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("insider_trades", captured))

    result = p.insider_trades("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/insider-trading/search"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("limit") == "200"
    assert result == _load_fixture("insider_trades")


# --- institutional holders (13F) --------------------------------------------


def test_institutional_holders_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("institutional_holders", captured))

    result = p.institutional_holders("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/institutional-ownership/extract-analytics/holder"
    assert req.url.params.get("symbol") == "NVDA"
    assert result == _load_fixture("institutional_holders")


# --- earnings calendar -------------------------------------------------------


def test_earnings_calendar_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("earnings_calendar", captured))

    result = p.earnings_calendar("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/earnings"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("limit") == "40"
    assert result == _load_fixture("earnings_calendar")


# --- caching: distinct cache keys per method --------------------------------


def test_methods_use_distinct_cache_keys(tmp_path):
    """Each data type must cache under its own key so refetching one type
    doesn't clobber or shadow another."""
    cache = Cache(tmp_path)

    def handler(request):
        path = request.url.path
        if "profile" in path:
            return httpx.Response(200, json=_load_fixture("profile"))
        if "income-statement" in path:
            return httpx.Response(200, json=_load_fixture("income_annual"))
        return httpx.Response(200, json=[])

    settings = Settings(fmp_api_key="testkey")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = FMPProvider(settings, cache, client=client)

    p.profile("NVDA")
    p.income_annual("NVDA")

    assert cache.get("NVDA", "profile") == _load_fixture("profile")
    assert cache.get("NVDA", "income_annual") == _load_fixture("income_annual")


def test_get_json_serves_from_cache_without_hitting_transport(tmp_path):
    cache = Cache(tmp_path)
    cache.put("NVDA", "profile", _load_fixture("profile"))

    def handler(request):
        raise AssertionError("transport should not be called on cache hit")

    settings = Settings(fmp_api_key="testkey")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = FMPProvider(settings, cache, client=client)

    result = p.profile("NVDA")
    assert result == _load_fixture("profile")


# --- market-wide feeds (Phase 1) --------------------------------------------


def _json_handler(payload, captured):
    """Handler that records the request and replies with a fixed JSON payload."""

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, json=payload)

    return handler


def test_biggest_gainers_url_and_namespace(tmp_path):
    captured = {}
    rows = [{"symbol": "AAA", "changesPercentage": 5.0}]
    p = _make_provider(tmp_path, _json_handler(rows, captured))

    result = p.biggest_gainers()

    req = captured["request"]
    assert req.url.path == "/stable/biggest-gainers"
    assert req.url.params.get("apikey") is not None
    assert result == rows
    # cached under the shared market namespace, not a ticker
    assert p.cache.get("_market", "biggest_gainers") == rows


def test_biggest_losers_and_most_actives_urls(tmp_path):
    cap1, cap2 = {}, {}
    p1 = _make_provider(tmp_path / "a", _json_handler([], cap1))
    p1.biggest_losers()
    assert cap1["request"].url.path == "/stable/biggest-losers"

    p2 = _make_provider(tmp_path / "b", _json_handler([], cap2))
    p2.most_actives()
    assert cap2["request"].url.path == "/stable/most-actives"


def test_quote_passes_symbol_including_index_caret(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _json_handler([{"symbol": "^GSPC"}], captured))

    p.quote("^GSPC")

    req = captured["request"]
    assert req.url.path == "/stable/quote"
    assert req.url.params.get("symbol") == "^GSPC"


def test_sector_snapshot_uses_date_in_url_and_cache_key(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _json_handler([{"sector": "Energy"}], captured))

    p.sector_snapshot(date(2026, 7, 16))

    req = captured["request"]
    assert req.url.path == "/stable/sector-performance-snapshot"
    assert req.url.params.get("date") == "2026-07-16"
    assert p.cache.get("_market", "sector_2026-07-16") == [{"sector": "Energy"}]


def test_treasury_rates_and_economic_indicator_urls(tmp_path):
    cap1, cap2 = {}, {}
    p1 = _make_provider(tmp_path / "a", _json_handler([], cap1))
    p1.treasury_rates()
    assert cap1["request"].url.path == "/stable/treasury-rates"

    p2 = _make_provider(tmp_path / "b", _json_handler([], cap2))
    p2.economic_indicator("GDP")
    assert cap2["request"].url.path == "/stable/economic-indicators"
    assert cap2["request"].url.params.get("name") == "GDP"


def test_market_methods_return_none_when_unavailable(tmp_path):
    def handler(request):
        raise AssertionError("transport should not be called when unavailable")

    p = _make_provider(tmp_path, handler, fmp_api_key=None)

    assert p.biggest_gainers() is None
    assert p.biggest_losers() is None
    assert p.most_actives() is None
    assert p.quote("^GSPC") is None
    assert p.sector_snapshot(date(2026, 7, 16)) is None
    assert p.treasury_rates() is None
    assert p.economic_indicator("GDP") is None
