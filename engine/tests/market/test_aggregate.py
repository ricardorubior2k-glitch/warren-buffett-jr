"""Tests for wbj.market.aggregate — market-panel normalization (Phase 1)."""

from datetime import date

from wbj.market import aggregate as agg


class StubFinnhub:
    """Stand-in for FinnhubProvider returning canned news payloads."""

    def __init__(self, general=None, company=None, quotes=None):
        self._general = general
        self._company = company
        self._quotes = quotes or {}
        self.company_calls = []

    def general_news(self, category="general"):
        return self._general

    def company_news(self, t, frm, to):
        self.company_calls.append((t, frm, to))
        return self._company

    def realtime_quote(self, t):
        return self._quotes.get(t.upper())


def _article(headline, ts, source="Reuters", url="http://x", related="AAPL"):
    return {"headline": headline, "datetime": ts, "source": source,
            "url": url, "related": related, "summary": "…", "id": ts}


class StubFMP:
    """Minimal stand-in for FMPProvider returning canned payloads.

    Any endpoint not explicitly set returns None, exercising the graceful-
    degradation paths in the aggregation layer.
    """

    def __init__(self, **payloads):
        self._p = payloads
        self.sector_calls = []

    def biggest_gainers(self):
        return self._p.get("gainers")

    def biggest_losers(self):
        return self._p.get("losers")

    def most_actives(self):
        return self._p.get("actives")

    def treasury_rates(self):
        return self._p.get("treasury")

    def economic_indicator(self, name):
        return self._p.get("econ", {}).get(name)

    def sector_snapshot(self, day):
        self.sector_calls.append(day)
        return self._p.get("sectors", {}).get(day.isoformat())


# --- movers -----------------------------------------------------------------


def _mover(sym, pct, price=100.0):
    return {"symbol": sym, "name": f"{sym} Inc", "price": price,
            "change": 1.0, "changesPercentage": pct, "exchange": "NASDAQ"}


def test_movers_normalizes_and_trims():
    fmp = StubFMP(
        gainers=[_mover("AAA", 5.1), _mover("BBB", 4.2), _mover("CCC", 3.3)],
        losers=[_mover("DDD", -6.0)],
        actives=[_mover("EEE", 0.5)],
    )
    out = agg.movers(fmp, limit=2)
    assert [g["symbol"] for g in out["gainers"]] == ["AAA", "BBB"]  # trimmed to 2
    assert out["gainers"][0]["change_pct"] == 5.1
    assert out["losers"][0]["symbol"] == "DDD"
    assert out["actives"][0]["symbol"] == "EEE"


def test_movers_empty_when_provider_returns_none():
    out = agg.movers(StubFMP())
    assert out == {"gainers": [], "losers": [], "actives": []}


def test_movers_drops_rows_without_symbol():
    fmp = StubFMP(gainers=[{"changesPercentage": 3.0}, _mover("OK", 2.0)])
    out = agg.movers(fmp)
    assert [g["symbol"] for g in out["gainers"]] == ["OK"]


# --- heatmap ----------------------------------------------------------------


def test_heatmap_uses_today_when_available_and_sorts_desc():
    day = date(2026, 7, 17)
    fmp = StubFMP(sectors={
        "2026-07-17": [
            {"sector": "Energy", "averageChange": -1.3},
            {"sector": "Technology", "averageChange": 1.9},
            {"sector": "Financials", "averageChange": 0.4},
        ]
    })
    out = agg.heatmap(fmp, today=day)
    assert out["date"] == "2026-07-17"
    assert [s["sector"] for s in out["sectors"]] == ["Technology", "Financials", "Energy"]
    assert out["sectors"][0]["change_pct"] == 1.9


def test_heatmap_walks_back_over_weekend():
    # today has no snapshot (e.g. Sunday); the prior Friday does.
    day = date(2026, 7, 17)
    fmp = StubFMP(sectors={
        "2026-07-15": [{"sector": "Technology", "averageChange": 2.0}],
    })
    out = agg.heatmap(fmp, today=day)
    assert out["date"] == "2026-07-15"
    assert out["sectors"][0]["sector"] == "Technology"
    # walked back day by day: 07-17, 07-16, then hit 07-15.
    assert fmp.sector_calls[:3] == [date(2026, 7, 17), date(2026, 7, 16), date(2026, 7, 15)]


def test_heatmap_empty_when_no_recent_snapshot():
    out = agg.heatmap(StubFMP(), today=date(2026, 7, 17))
    assert out == {"date": None, "sectors": []}


# --- macro ------------------------------------------------------------------


def test_macro_builds_ordered_curve_from_newest_row():
    fmp = StubFMP(
        treasury=[
            {"date": "2026-07-15", "month3": 3.83, "year10": 4.55, "year30": 5.08},
            {"date": "2026-07-16", "month3": 3.84, "year1": 3.99, "year10": 4.57, "year30": 5.09},
        ],
        econ={"GDP": [{"date": "2026-06-30", "value": 29123.4}]},
    )
    out = agg.macro(fmp)
    assert out["curve_date"] == "2026-07-16"  # newest row chosen
    curve = {c["tenor"]: c["rate"] for c in out["curve"]}
    assert curve == {"3M": 3.84, "1Y": 3.99, "10Y": 4.57, "30Y": 5.09}
    # ordered by maturity, not dict order
    assert [c["tenor"] for c in out["curve"]] == ["3M", "1Y", "10Y", "30Y"]
    assert out["indicators"]["GDP"]["value"] == 29123.4


def test_macro_empty_curve_when_unavailable():
    out = agg.macro(StubFMP())
    assert out == {"curve_date": None, "curve": [], "indicators": {}}


# --- news -------------------------------------------------------------------


def test_market_news_normalizes_sorts_newest_first_and_trims():
    fh = StubFinnhub(general=[
        _article("Old story", 1_784_000_000),
        _article("Newest story", 1_784_300_000),
        _article("Middle story", 1_784_100_000),
    ])
    out = agg.market_news(fh, limit=2)
    heads = [a["headline"] for a in out["articles"]]
    assert heads == ["Newest story", "Middle story"]  # newest first, trimmed to 2
    a = out["articles"][0]
    assert a["source"] == "Reuters" and a["url"] == "http://x"
    assert a["datetime"].startswith("2026-")  # unix ts -> ISO string
    assert "ts" not in a  # internal sort key stripped


def test_market_news_drops_articles_without_headline_or_url():
    fh = StubFinnhub(general=[
        {"headline": "", "url": "http://x", "datetime": 1},
        {"headline": "No url", "url": "", "datetime": 2},
        _article("Keep me", 3),
    ])
    out = agg.market_news(fh)
    assert [a["headline"] for a in out["articles"]] == ["Keep me"]


def test_market_news_empty_when_unavailable():
    assert agg.market_news(StubFinnhub()) == {"articles": []}


def test_company_news_uses_two_week_window_and_tags_ticker():
    fh = StubFinnhub(company=[_article("Apple thing", 1_784_300_000)])
    out = agg.company_news(fh, "aapl", limit=5, today=date(2026, 7, 17))
    assert out["ticker"] == "AAPL"
    assert out["articles"][0]["headline"] == "Apple thing"
    # queried the 14-day window ending today
    t, frm, to = fh.company_calls[0]
    assert t == "aapl" and to == date(2026, 7, 17) and frm == date(2026, 7, 3)


# --- live stream tick -------------------------------------------------------


def test_stream_event_carries_seq_time_and_normalized_quotes():
    from datetime import datetime, timezone

    fh = StubFinnhub(quotes={
        "AAPL": {"c": 333.74, "dp": 1.76, "pc": 327.5},
        "NVDA": {"c": 202.81, "dp": 1.21, "pc": 200.4},
    })
    now = datetime(2026, 7, 18, 15, 0, tzinfo=timezone.utc)
    ev = agg.stream_event(fh, ["aapl", "nvda"], seq=7, now=now)
    assert ev["seq"] == 7
    assert ev["ts"] == now.isoformat()
    assert ev["quotes"] == [
        {"symbol": "AAPL", "price": 333.74, "change_pct": 1.76, "prev_close": 327.5},
        {"symbol": "NVDA", "price": 202.81, "change_pct": 1.21, "prev_close": 200.4},
    ]


def test_stream_event_omits_symbols_without_price_but_keeps_heartbeat():
    fh = StubFinnhub(quotes={"AAPL": {"c": 0}, "NVDA": None})  # no usable price
    ev = agg.stream_event(fh, ["AAPL", "NVDA"], seq=1)
    assert ev["quotes"] == []          # nothing pushed
    assert ev["seq"] == 1 and ev["ts"]  # but the tick still beats
