"""Filesystem-backed JSON response cache for wbj providers.

File layout: `<cache_dir>/<TICKER>/<key>.json` containing
`{"fetched_at": iso8601 UTC, "payload": ...}`.

This module reads the wall clock (`datetime.now(timezone.utc)`) to stamp
and age cache entries — that is infrastructure bookkeeping, not analysis
math, and is exempt from the engine's null-state/lineage discipline
(see `wbj.core.nullstates`).

Writes are atomic (temp file + `os.replace`) so the cache is safe under
concurrent access: the web app serves requests on many threads, and
several `Cache` instances (one per provider bundle) point at the same
directory. A reader therefore always sees a complete record — never a
half-written file — and concurrent writers to the same key resolve to a
last-writer-wins replace with no corruption.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# On Windows, os.replace() raises PermissionError if another thread holds the
# target open for reading. Readers are brief, so retry the swap a few times
# before giving up. Caching is best-effort: a skipped write just re-fetches later.
_REPLACE_RETRIES = 20
_REPLACE_BACKOFF_S = 0.003


class Cache:
    """Filesystem-backed JSON cache, keyed by ticker and cache key."""

    def __init__(self, cache_dir: Path | str) -> None:
        self.cache_dir = Path(cache_dir)

    def _path(self, ticker: str, key: str) -> Path:
        return self.cache_dir / ticker / f"{key}.json"

    def _read_record(self, ticker: str, key: str) -> dict | None:
        path = self._path(ticker, key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def get(self, ticker: str, key: str) -> dict | None:
        """Return the cached payload for (ticker, key), or None if absent/corrupt."""
        record = self._read_record(ticker, key)
        if record is None:
            return None
        return record.get("payload")

    def put(self, ticker: str, key: str, payload: dict) -> None:
        """Write payload to cache, stamped with the current UTC time.

        Atomic: the record is written to a unique temp file in the same
        directory and then `os.replace`d onto the target (atomic on POSIX
        and Windows). Concurrent readers never observe a partial file, and
        concurrent writers resolve to last-writer-wins without corruption.
        """
        path = self._path(ticker, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{key}.", suffix=".tmp")
        except OSError:
            return  # can't stage the write — skip caching, non-fatal
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(record))
            for attempt in range(_REPLACE_RETRIES):
                try:
                    os.replace(tmp, path)  # atomic swap onto the target
                    return
                except PermissionError:
                    # Windows: a reader holds the target open; back off and retry.
                    if attempt == _REPLACE_RETRIES - 1:
                        break
                    time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))
        finally:
            # remove the temp if the swap never happened (contention or write error)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def age_days(self, ticker: str, key: str) -> float | None:
        """Return the cache entry's age in days, or None if absent/corrupt."""
        record = self._read_record(ticker, key)
        if record is None:
            return None
        try:
            fetched_at = datetime.fromisoformat(record["fetched_at"])
        except (KeyError, ValueError, TypeError):
            return None
        delta = datetime.now(timezone.utc) - fetched_at
        return delta.total_seconds() / 86400.0
