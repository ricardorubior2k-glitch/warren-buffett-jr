"""Tests for wbj.providers.cache.Cache and wbj.providers.base.Provider."""

import json
from datetime import datetime, timedelta, timezone

import httpx

from wbj.providers.base import Provider
from wbj.providers.cache import Cache


# --- Cache ---------------------------------------------------------------


def test_cache_roundtrip(tmp_path):
    c = Cache(tmp_path)
    c.put("NVDA", "profile", {"name": "NVIDIA"})
    assert c.get("NVDA", "profile")["name"] == "NVIDIA"
    assert c.age_days("NVDA", "profile") < 1 / 24


def test_cache_get_missing_returns_none(tmp_path):
    c = Cache(tmp_path)
    assert c.get("NVDA", "profile") is None


def test_cache_age_days_missing_returns_none(tmp_path):
    c = Cache(tmp_path)
    assert c.age_days("NVDA", "profile") is None


def test_cache_writes_expected_file_layout(tmp_path):
    c = Cache(tmp_path)
    c.put("NVDA", "profile", {"name": "NVIDIA"})
    path = tmp_path / "NVDA" / "profile.json"
    assert path.exists()
    record = json.loads(path.read_text())
    assert record["payload"] == {"name": "NVIDIA"}
    assert "fetched_at" in record
    # fetched_at must parse as an ISO-8601 UTC timestamp
    parsed = datetime.fromisoformat(record["fetched_at"])
    assert parsed.tzinfo is not None


# --- Provider.get_json: cache-first --------------------------------------


def test_get_json_serves_from_cache_without_hitting_transport(tmp_path):
    cache = Cache(tmp_path)
    cache.put("NVDA", "profile", {"name": "NVIDIA"})

    def handler(request):
        raise AssertionError("transport should not be called on cache hit")

    p = Provider(
        settings=None,
        cache=cache,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = p.get_json("https://x.test/a", {}, "profile", "NVDA")
    assert result == {"name": "NVIDIA"}


def test_get_json_refetches_when_cache_stale(tmp_path):
    cache = Cache(tmp_path)
    cache.put("NVDA", "profile", {"name": "OLD"})
    path = tmp_path / "NVDA" / "profile.json"
    record = json.loads(path.read_text())
    record["fetched_at"] = (
        datetime.now(timezone.utc) - timedelta(days=10)
    ).isoformat()
    path.write_text(json.dumps(record))

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"name": "NEW"})

    p = Provider(
        settings=None,
        cache=cache,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = p.get_json("https://x.test/a", {}, "profile", "NVDA", max_age_days=1)
    assert result == {"name": "NEW"}
    assert calls["n"] == 1


def test_get_json_serves_fresh_cache_within_max_age(tmp_path):
    cache = Cache(tmp_path)
    cache.put("NVDA", "profile", {"name": "FRESH"})

    def handler(request):
        raise AssertionError("transport should not be called for fresh cache")

    p = Provider(
        settings=None,
        cache=cache,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = p.get_json("https://x.test/a", {}, "profile", "NVDA", max_age_days=1)
    assert result == {"name": "FRESH"}


# --- Provider.get_json: retry / backoff -----------------------------------


def test_get_json_retries_twice_then_succeeds(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"name": "NVIDIA"})

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sleeps = []
    p._sleep = lambda seconds: sleeps.append(seconds)

    result = p.get_json("https://x.test/a", {}, "profile", "NVDA")
    assert result == {"name": "NVIDIA"}
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]


def test_provider_returns_none_after_3_failures(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p._sleep = lambda seconds: None

    assert p.get_json("https://x.test/a", {}, "k", "NVDA") is None
    assert calls["n"] == 3


def test_get_json_backoff_schedule_is_exponential(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sleeps = []
    p._sleep = lambda seconds: sleeps.append(seconds)

    p.get_json("https://x.test/a", {}, "k", "NVDA")
    # 3 total attempts => backoff sleeps only between attempts (2 gaps),
    # no sleep after the final, exhausted attempt.
    assert sleeps == [0.5, 1.0]


def test_get_json_does_not_retry_on_4xx(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404)

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p._sleep = lambda seconds: None

    assert p.get_json("https://x.test/a", {}, "k", "NVDA") is None
    assert calls["n"] == 1


def test_get_json_retries_on_timeout(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"name": "NVIDIA"})

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p._sleep = lambda seconds: None

    result = p.get_json("https://x.test/a", {}, "profile", "NVDA")
    assert result == {"name": "NVIDIA"}
    assert calls["n"] == 2


def test_get_json_passes_through_custom_headers(tmp_path):
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = p.get_json(
        "https://x.test/a", {}, "k", "NVDA", headers={"User-Agent": "test-agent"}
    )

    assert result == {"ok": True}
    assert captured["headers"].get("user-agent") == "test-agent"


def test_get_json_omitted_headers_does_not_break_request(tmp_path):
    """Existing callers that don't pass `headers` must be unaffected."""

    def handler(request):
        return httpx.Response(200, json={"ok": True})

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert p.get_json("https://x.test/a", {}, "k", "NVDA") == {"ok": True}


def test_get_json_returns_none_on_malformed_json_body(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"not json", headers={"content-type": "application/json"})

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p._sleep = lambda seconds: None

    assert p.get_json("https://x.test/a", {}, "k", "NVDA") is None


def test_get_json_never_raises_on_transport_error(tmp_path):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p._sleep = lambda seconds: None

    assert p.get_json("https://x.test/a", {}, "k", "NVDA") is None


# --- Provider.get_json: caching successful responses ----------------------


def test_get_json_caches_successful_response(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"name": "NVIDIA"})

    cache = Cache(tmp_path)
    p = Provider(
        settings=None,
        cache=cache,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p.get_json("https://x.test/a", {}, "profile", "NVDA")
    assert cache.get("NVDA", "profile") == {"name": "NVIDIA"}


# --- Provider.get_json: apikey never logged --------------------------------


def test_get_json_does_not_log_apikey_value(tmp_path, caplog):
    def handler(request):
        return httpx.Response(500)

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p._sleep = lambda seconds: None

    with caplog.at_level("WARNING"):
        p.get_json("https://x.test/a", {"apikey": "SUPERSECRET"}, "k", "NVDA")

    assert "SUPERSECRET" not in caplog.text


def test_get_json_does_not_log_token_value(tmp_path, caplog):
    def handler(request):
        return httpx.Response(404)

    p = Provider(
        settings=None,
        cache=Cache(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p._sleep = lambda seconds: None

    with caplog.at_level("WARNING"):
        p.get_json("https://x.test/a", {"token": "SUPERSECRET"}, "k", "NVDA")

    assert "SUPERSECRET" not in caplog.text


# --- concurrency (thread-safe atomic writes) --------------------------------


def test_cache_concurrent_writes_never_corrupt(tmp_path):
    """Many threads writing+reading the same key must never yield a corrupt
    (half-written) record, and no temp files must be left behind."""
    import threading

    c = Cache(tmp_path)
    c.put("NVDA", "hot", {"n": 0})  # seed so readers always find a file
    errors: list = []
    values_seen: set = set()

    def writer(n):
        try:
            for _ in range(40):
                c.put("NVDA", "hot", {"n": n, "pad": "x" * 500})
        except Exception as e:  # pragma: no cover - failure path
            errors.append(("write", e))

    def reader():
        try:
            for _ in range(80):
                v = c.get("NVDA", "hot")
                # never a partial parse: get() returns either a valid dict or None
                if v is not None:
                    values_seen.add(v["n"])
        except Exception as e:  # a corrupt read would raise (e.g. KeyError)
            errors.append(("read", e))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    threads += [threading.Thread(target=reader) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent access raised: {errors}"
    # the final record is one complete, valid write from some writer
    final = c.get("NVDA", "hot")
    assert final is not None and final["n"] in set(range(6)) | {0}
    # no orphaned temp files left in the ticker directory
    leftovers = list((tmp_path / "NVDA").glob("*.tmp"))
    assert leftovers == [], f"orphaned temp files: {leftovers}"
