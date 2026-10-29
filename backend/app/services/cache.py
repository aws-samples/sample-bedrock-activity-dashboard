"""Thread-safe TTL cache utilities for expensive, user-agnostic reads.

FastAPI runs synchronous route handlers in a threadpool, so any process-wide
cache shared across requests must be safe for concurrent access. Every
operation here is guarded by a lock.

These caches are deliberately **user-agnostic**: callers must only ever store
raw, unfiltered data keyed by non-identity attributes (e.g. a coarse time
bucket). Per-user scoping and filtering must always run per-request *after* a
cache lookup so that a shared cache entry can never leak one user's data to
another.
"""

import threading
import time
from typing import Any, Hashable, Optional


class TTLCache:
    """A minimal thread-safe time-to-live cache backed by a locked dict.

    Entries expire ``ttl_seconds`` after they are written. Expired entries are
    evicted lazily on access. All reads and writes are serialized through a
    single lock, which is sufficient for the low-cardinality, coarse-grained
    keys these caches use.
    """

    def __init__(self, ttl_seconds: float):
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        # key -> (expires_at_monotonic, value)
        self._store: dict[Hashable, tuple[float, Any]] = {}

    def get(self, key: Hashable) -> Optional[Any]:
        """Return the cached value for ``key`` or None if missing/expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if now >= expires_at:
                # Expired — evict and report a miss.
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: Hashable, value: Any) -> None:
        """Store ``value`` under ``key`` with the configured TTL."""
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        """Drop all cached entries. Primarily used to isolate tests."""
        with self._lock:
            self._store.clear()
