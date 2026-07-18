"""Tests for wbj.search.rank — company search tier ranking."""

from wbj.search import DEFAULT_LIMIT, rank


def _e(ticker, title):
    return {"cik_str": 1, "ticker": ticker, "title": title}


# A small universe exercising every tier and some overlaps.
UNIVERSE = [
    _e("AAPL", "Apple Inc."),
    _e("APP", "Applovin Corp"),
    _e("APPS", "Digital Turbine Inc"),        # ticker-prefix, name unrelated
    _e("MSFT", "Microsoft Corp"),
    _e("MU", "Micron Technology Inc"),
    _e("MSTR", "Microstrategy Inc"),
    _e("FSLR", "First Solar Inc"),            # name contains "SOLAR"
    _e("RUN", "Sunrun Inc"),                  # name contains "SOLAR"? no
    _e("NVDA", "NVIDIA Corp"),
]


def test_empty_query_returns_empty():
    assert rank(UNIVERSE, "") == []
    assert rank(UNIVERSE, "   ") == []


def test_exact_ticker_ranks_first():
    out = rank(UNIVERSE, "AAPL")
    assert out[0] == {"ticker": "AAPL", "name": "Apple Inc."}


def test_ticker_prefix_ranked_before_name_matches_and_sorted():
    # "APP" -> ticker prefix APP, APPS (sorted by ticker), then name-prefix none,
    # then name-contains "Applovin"? Applovin's ticker is APP (exact-prefix already).
    out = rank(UNIVERSE, "APP")
    tickers = [r["ticker"] for r in out]
    # APP and APPS are ticker-prefix matches, alphabetical by ticker
    assert tickers[:2] == ["APP", "APPS"]


def test_name_prefix_beats_name_contains():
    """The core fix: typing a name lists companies whose NAME starts with it,
    ahead of companies that merely contain the letters mid-name."""
    universe = [
        _e("XONE", "Onelink Something"),       # name starts with "ONE"
        _e("ABCD", "Verizone Onetime"),        # contains "ONE" mid-name
        _e("ONON", "On Holding"),              # ticker prefix "ON"? query is ONE
    ]
    out = rank(universe, "ONE")
    names = [r["name"] for r in out]
    assert names[0] == "Onelink Something"     # name-prefix first
    assert "Verizone Onetime" in names         # contains still included
    assert names.index("Onelink Something") < names.index("Verizone Onetime")


def test_name_prefix_lists_all_matches_sorted_alphabetically():
    out = rank(UNIVERSE, "MICRO")
    names = [r["name"] for r in out]
    # Micron, Microsoft, Microstrategy all start with "Micro" -> alphabetical
    assert names == ["Micron Technology Inc", "Microsoft Corp", "Microstrategy Inc"]


def test_name_contains_is_lowest_tier():
    out = rank(UNIVERSE, "SOLAR")
    assert [r["ticker"] for r in out] == ["FSLR"]  # only First Solar contains it


def test_case_insensitive():
    assert rank(UNIVERSE, "aapl")[0]["ticker"] == "AAPL"
    assert rank(UNIVERSE, "micro") == rank(UNIVERSE, "MICRO")


def test_limit_is_respected():
    big = [_e(f"T{i:03d}", f"Micro Co {i:03d}") for i in range(50)]
    assert len(rank(big, "MICRO")) == DEFAULT_LIMIT          # default cap
    assert len(rank(big, "MICRO", limit=5)) == 5             # custom cap
    assert DEFAULT_LIMIT == 15


def test_skips_entries_without_ticker_or_malformed():
    universe = [
        {"cik_str": 1, "title": "No Ticker Co"},   # missing ticker
        {"ticker": "", "title": "Empty Ticker"},   # empty ticker
        "not a dict",                                # malformed
        _e("GOOD", "Good Micro Corp"),
    ]
    out = rank(universe, "MICRO")
    assert [r["ticker"] for r in out] == ["GOOD"]
