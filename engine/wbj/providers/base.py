"""Resilient HTTP provider base: cache-first fetch with retry/backoff.

`Provider.get_json` never raises for network/HTTP failures — it returns
`None` on exhaustion, and callers are expected to map that to
`wbj.core.nullstates.NullState.MISSING`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from wbj.providers.cache import Cache

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_REDACTED_PARAMS = frozenset({"apikey", "token", "api_key"})

# The web app serves requests concurrently, so many provider calls can be in
# flight at once (e.g. the discovery screener plus a live quote). Cap the
# number of *simultaneous outbound HTTP calls* process-wide so bursts don't
# trip data-provider rate limits (HTTP 429). This is a concurrency cap, not a
# per-second token bucket — cache hits skip it entirely and stay instant.
MAX_CONCURRENT_REQUESTS = 6
_outbound = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)


class TokenBucket:
    """Thread-safe token bucket for pacing outbound calls to a provider.

    Refills `rate` tokens per second up to `capacity`. `acquire()` blocks
    until a token is available (or `timeout` elapses), smoothing bursts so a
    concurrent workload — e.g. the screener scoring many tickers — does not
    exceed a provider's requests-per-minute limit.
    """

    def __init__(self, rate_per_sec: float, capacity: float) -> None:
        self.rate = rate_per_sec
        self.capacity = capacity
        self._tokens = capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
                wait = (1 - self._tokens) / self.rate
            if deadline is not None and time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 0.25))


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds form) into seconds, capped."""
    if not value:
        return None
    try:
        return min(float(value), 30.0)  # cap so a huge value can't hang a request
    except ValueError:
        return None  # HTTP-date form not supported; use default backoff


def _redact_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Copy `params` with sensitive values masked, safe to put in log text."""
    if not params:
        return {}
    return {
        k: ("***" if k.lower() in _REDACTED_PARAMS else v) for k, v in params.items()
    }


class Provider:
    """Base class for wbj data providers.

    Subclasses build request URLs/params and call `get_json`, which
    handles cache-first serving and resilient retries uniformly.

    A subclass may set a class-level `rate_bucket` (a `TokenBucket`) to pace
    its outbound calls; the default `None` means no pacing.
    """

    # Opt-in per-provider request pacing; overridden by subclasses that need it.
    rate_bucket: "TokenBucket | None" = None

    def __init__(
        self,
        settings: Any,
        cache: Cache,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.client = client if client is not None else httpx.Client()

    def _sleep(self, seconds: float) -> None:
        """Sleep for `seconds`. Isolated so tests can monkeypatch it out."""
        time.sleep(seconds)

    def get_json(
        self,
        url: str,
        params: dict[str, Any],
        cache_key: str,
        ticker: str,
        max_age_days: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict | None:
        """Fetch JSON, cache-first, with retry/backoff on transient failures.

        If a cache entry exists for (ticker, cache_key) and is fresh enough
        (age <= max_age_days, or max_age_days is None), it is returned
        without touching the network. Otherwise up to 3 attempts are made
        against `url`, backing off 0.5s/1s/2s between attempts on 5xx
        responses or httpx transport errors (including timeouts). 4xx
        responses are treated as non-retryable client errors. Returns None
        (never raises) if the fetch ultimately fails; a successful response
        is written to cache before being returned.

        `headers`, if given, is passed through to the underlying request
        (e.g. a required `User-Agent` per SEC EDGAR's fair-access policy).
        Existing callers that don't pass `headers` are unaffected.
        """
        age = self.cache.age_days(ticker, cache_key)
        if age is not None and (max_age_days is None or age <= max_age_days):
            return self.cache.get(ticker, cache_key)

        safe_params = _redact_params(params)

        for attempt in range(_MAX_ATTEMPTS):
            is_last_attempt = attempt == _MAX_ATTEMPTS - 1
            try:
                if self.rate_bucket is not None:
                    self.rate_bucket.acquire()  # pace outbound calls (req/min)
                with _outbound:  # cap simultaneous outbound calls (rate-limit guard)
                    response = self.client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                logger.warning(
                    "wbj provider request failed (attempt %d/%d) url=%s "
                    "params=%s error=%s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    url,
                    safe_params,
                    exc,
                )
                if not is_last_attempt:
                    self._sleep(_BACKOFF_SECONDS[attempt])
                continue

            if response.status_code < 400:
                try:
                    payload = response.json()
                except ValueError:
                    logger.warning(
                        "wbj provider returned malformed JSON status=%d url=%s "
                        "params=%s",
                        response.status_code,
                        url,
                        safe_params,
                    )
                    return None
                self.cache.put(ticker, cache_key, payload)
                return payload

            if response.status_code == 429:
                # Rate-limited: retryable. Honor Retry-After when present,
                # otherwise fall back to the standard backoff schedule.
                logger.warning(
                    "wbj provider rate-limited (attempt %d/%d) url=%s params=%s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    url,
                    safe_params,
                )
                if not is_last_attempt:
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                    self._sleep(retry_after if retry_after is not None
                                else _BACKOFF_SECONDS[attempt])
                continue

            if response.status_code < 500:
                logger.warning(
                    "wbj provider client error status=%d url=%s params=%s",
                    response.status_code,
                    url,
                    safe_params,
                )
                return None

            logger.warning(
                "wbj provider server error (attempt %d/%d) status=%d url=%s "
                "params=%s",
                attempt + 1,
                _MAX_ATTEMPTS,
                response.status_code,
                url,
                safe_params,
            )
            if not is_last_attempt:
                self._sleep(_BACKOFF_SECONDS[attempt])

        # Network exhausted (429 rate-limit, 5xx, or transport error). Rather
        # than blanking the panel, fall back to the last cached copy regardless
        # of age — the terminal degrades to delayed real data instead of
        # "sin datos". Common when a data provider's daily quota is reached.
        stale = self.cache.get(ticker, cache_key)
        if stale is not None:
            logger.info(
                "wbj serving stale cache after fetch failure url=%s params=%s",
                url, safe_params,
            )
            return stale
        return None
