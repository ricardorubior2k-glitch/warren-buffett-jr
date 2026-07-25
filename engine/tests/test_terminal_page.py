"""The Phase 2 terminal page exists and is wired to the live endpoints."""

from pathlib import Path

_TERMINAL = Path(__file__).parent.parent / "scripts" / "terminal.html"


def test_terminal_html_exists():
    assert _TERMINAL.exists(), "scripts/terminal.html is missing"


def test_terminal_fetches_the_market_and_analysis_endpoints():
    html = _TERMINAL.read_text(encoding="utf-8")
    for endpoint in ("/api/market/movers", "/api/market/heatmap",
                     "/api/analyze?ticker="):
        assert endpoint in html, f"terminal.html does not call {endpoint}"


def test_terminal_wires_the_phase3_score_backed_panels():
    html = _TERMINAL.read_text(encoding="utf-8")
    # WBJ differentiators: scored watchlist, discovery screener, saved theses
    for endpoint in ("/api/quickscore?ticker=", "/api/screen", "/api/memoria"):
        assert endpoint in html, f"terminal.html does not call {endpoint}"


def test_terminal_wires_the_finnhub_news_panels():
    html = _TERMINAL.read_text(encoding="utf-8")
    # market news ticker + per-company news in the detail panel
    for endpoint in ("/api/market/news", "/api/news?ticker="):
        assert endpoint in html, f"terminal.html does not call {endpoint}"


def test_terminal_is_a_complete_document():
    html = _TERMINAL.read_text(encoding="utf-8")
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "</html>" in html
