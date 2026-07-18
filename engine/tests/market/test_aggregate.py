"""Tests for wbj.market.aggregate — market-panel normalization (Phase 1)."""

from datetime import date

from wbj.market import aggregate as agg


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
