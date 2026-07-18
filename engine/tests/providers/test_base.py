"""Tests for wbj.providers.base: param redaction in logged requests."""

import logging
import time

import httpx

from wbj.config import Settings
from wbj.providers.base import Provider
from wbj.providers.cache import Cache


def _make_provider(tmp_path, handler):
    settings = Settings()
    cache = Cache(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Provider(settings, cache, client=client)


def test_redacts_apikey_token_and_api_key_from_client_error_log(tmp_path, caplog):
    """4xx responses log params; apikey/token/api_key must never appear in
    plaintext in the log output — only the '***' mask."""

    def handler(request):
        return httpx.Response(400, json={"error": "bad request"})

    p = _make_provider(tmp_path, handler)

    with caplog.at_level(logging.WARNING):
        result = p.get_json(
            "https://example.com/thing",
            {
                "apikey": "secret-fmp-key",
                "token": "secret-finnhub-key",
                "api_key": "secret-fred-key",
                "symbol": "NVDA",
            },
            "thing",
            "NVDA",
        )

    assert result is None
    log_text = caplog.text
    assert "secret-fmp-key" not in log_text
    assert "secret-finnhub-key" not in log_text
    assert "secret-fred-key" not in log_text
    assert "NVDA" in log_text


def test_outbound_calls_are_capped_by_the_concurrency_semaphore(tmp_path):
    """Many threads fetching at once must never exceed MAX_CONCURRENT_REQUESTS
    simultaneous outbound HTTP calls (rate-limit guard)."""
    import threading

    from wbj.providers import base

    live = 0
    peak = 0
    mu = threading.Lock()

    def handler(request):
        nonlocal live, peak
        with mu:
            live += 1
            peak = max(peak, live)
        time.sleep(0.02)  # hold the "connection" so overlap is observable
        with mu:
            live -= 1
        return httpx.Response(200, json={"ok": True})

    def worker(i):
        # distinct cache keys so every call actually hits the network
        p = _make_provider(tmp_path, handler)
        p.get_json("https://x.test/a", {}, f"k{i}", "T")

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(base.MAX_CONCURRENT_REQUESTS * 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak <= base.MAX_CONCURRENT_REQUESTS, (
        f"peak concurrent outbound calls {peak} exceeded cap "
        f"{base.MAX_CONCURRENT_REQUESTS}"
    )
    # and the guard actually allowed real concurrency (not accidental serialization)
    assert peak > 1
