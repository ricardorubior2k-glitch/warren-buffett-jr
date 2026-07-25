"""Tests for wbj.providers.base: param redaction in logged requests."""

import logging
import time

import httpx
import pytest

from wbj.config import Settings
from wbj.providers import base as _base
from wbj.providers.base import Provider
from wbj.providers.cache import Cache


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """Isolate the process-wide breaker state between tests (deterministic)."""
    _base._breaker_state.clear()
    yield
    _base._breaker_state.clear()


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


# --- Circuit breaker --------------------------------------------------------


def test_breaker_opens_after_repeated_429_and_stops_hitting_the_network(tmp_path):
    """Once a host has exhausted _BREAKER_THRESHOLD times, later calls skip the
    network entirely instead of burning the full retry budget again."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={"Error Message": "Limit Reach"})

    p = _make_provider(tmp_path, handler)
    p._sleep = lambda s: None

    # Each exhausting get_json makes _MAX_ATTEMPTS network calls and records one
    # failure; after THRESHOLD failures the breaker opens.
    for _ in range(_base._BREAKER_THRESHOLD):
        assert p.get_json("https://quota.test/a", {"apikey": "K"}, "k", "T") is None
    calls_when_opened = calls["n"]
    assert calls_when_opened == _base._BREAKER_THRESHOLD * _base._MAX_ATTEMPTS

    # Breaker is now open: further calls must NOT touch the network.
    for _ in range(5):
        assert p.get_json("https://quota.test/a", {"apikey": "K"}, "k", "T") is None
    assert calls["n"] == calls_when_opened  # no new network calls


def test_open_breaker_still_serves_stale_cache_unchanged(tmp_path):
    """While open, the breaker returns the SAME cached fallback the retry path
    would have returned — data is unchanged, only the waiting is gone."""
    def handler(request):
        return httpx.Response(429, json={"Error Message": "Limit Reach"})

    p = _make_provider(tmp_path, handler)
    p._sleep = lambda s: None
    p.cache.put("T", "k", {"seed": 1})           # a prior good copy exists
    p.cache.age_days = lambda t, k: 999.0        # force it to look stale

    # Trip the breaker; each exhausting call still degrades to the stale copy.
    for _ in range(_base._BREAKER_THRESHOLD):
        assert p.get_json("https://quota.test/a", {}, "k", "T") == {"seed": 1}

    calls = {"n": 0}
    p.client = httpx.Client(transport=httpx.MockTransport(
        lambda r: (calls.__setitem__("n", calls["n"] + 1)) or httpx.Response(500)))
    # Open now: same stale value, and the network was never touched.
    assert p.get_json("https://quota.test/a", {}, "k", "T") == {"seed": 1}
    assert calls["n"] == 0


def test_breaker_is_per_host_and_does_not_blackhole_other_providers(tmp_path):
    """An exhausted FMP host must never trip the breaker for EDGAR/FinnHub."""
    def handler(request):
        if request.url.host == "down.test":
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"ok": True})

    p = _make_provider(tmp_path, handler)
    p._sleep = lambda s: None

    for _ in range(_base._BREAKER_THRESHOLD):     # open the breaker for down.test
        p.get_json("https://down.test/a", {}, "k1", "T")
    assert _base._breaker_is_open("down.test")
    assert not _base._breaker_is_open("healthy.test")

    # A different, healthy host is unaffected and returns live data.
    assert p.get_json("https://healthy.test/a", {}, "k2", "T") == {"ok": True}


def test_breaker_reopens_and_a_success_closes_it(tmp_path):
    """After the cooldown elapses (half-open) a successful response resets the
    breaker so the provider is used normally again once it recovers."""
    state = {"code": 429}

    def handler(request):
        return httpx.Response(state["code"], json={"ok": True})

    p = _make_provider(tmp_path, handler)
    p._sleep = lambda s: None

    for _ in range(_base._BREAKER_THRESHOLD):
        p.get_json("https://flap.test/a", {}, "k", "T")
    assert _base._breaker_is_open("flap.test")

    # Simulate the cooldown having elapsed -> half-open (probe allowed through).
    _base._breaker_state["flap.test"]["open_until"] = time.monotonic() - 1
    state["code"] = 200  # provider has recovered
    assert p.get_json("https://flap.test/a", {}, "k", "T") == {"ok": True}
    assert not _base._breaker_is_open("flap.test")  # success closed it
