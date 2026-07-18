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


# --- 429 rate-limit handling + TokenBucket ----------------------------------


def test_429_is_retried_then_succeeds(tmp_path):
    """A 429 is retryable: after backing off, a subsequent 200 is returned."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"Error Message": "Limit Reach"})
        return httpx.Response(200, json={"ok": True})

    p = _make_provider(tmp_path, handler)
    p._sleep = lambda s: None  # don't actually wait in tests

    result = p.get_json("https://x.test/a", {"apikey": "K"}, "k", "NVDA")

    assert result == {"ok": True}
    assert calls["n"] == 2  # retried once


def test_429_exhausts_to_none_without_raising(tmp_path):
    """Persistent 429s exhaust retries and return None (never raise)."""
    def handler(request):
        return httpx.Response(429, json={"Error Message": "Limit Reach"})

    p = _make_provider(tmp_path, handler)
    p._sleep = lambda s: None

    assert p.get_json("https://x.test/a", {"apikey": "K"}, "k", "NVDA") is None


def test_429_honors_retry_after_header(tmp_path):
    """Retry-After (delta-seconds) drives the wait instead of the default."""
    slept = []

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "7"}, json={})

    p = _make_provider(tmp_path, handler)
    p._sleep = lambda s: slept.append(s)

    p.get_json("https://x.test/a", {"apikey": "K"}, "k", "NVDA")

    assert 7.0 in slept  # used the header value, not the default backoff


def test_token_bucket_allows_burst_then_paces():
    from wbj.providers.base import TokenBucket

    tb = TokenBucket(rate_per_sec=1000.0, capacity=3.0)
    # capacity burst is immediate
    t0 = time.monotonic()
    assert all(tb.acquire() for _ in range(3))
    assert time.monotonic() - t0 < 0.05
    # the next token must wait for a refill (~1/1000s), still returns True
    assert tb.acquire(timeout=1.0) is True


def test_token_bucket_acquire_times_out_when_starved():
    from wbj.providers.base import TokenBucket

    tb = TokenBucket(rate_per_sec=0.001, capacity=1.0)
    assert tb.acquire() is True          # consume the only token
    assert tb.acquire(timeout=0.05) is False  # refill too slow -> timeout
