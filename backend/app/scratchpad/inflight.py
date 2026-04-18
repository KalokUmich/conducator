"""In-flight deduplication for expensive cache-miss computations.

Problem: when N worker threads concurrently miss the same cache key, all N
start the expensive computation. The first winner writes the cache, the
other N-1 waste CPU (and, for the PR Brain case, budget) doing the same
work. The sentry-006 eval burned ~7× expected time on `_ensure_graph`
because seven parallel sub-agents each rebuilt the 17K-file dependency
graph from cold.

Solution: a per-key threading.Lock. First caller acquires, does the work,
releases. Followers wait inside the lock, then re-check the cache under
the lock (double-checked locking) — by the time they acquire, the leader
has already written the result, so they return immediately.

Different keys never serialise against each other.

This is the Python/threading equivalent of Claude Code's async in-flight
dedup via shared `Promise` (reference/claude-code/utils/memoize.ts:120+);
our GIL + Lock model is simpler and strong enough for our thread-pool
tool executor.
"""

from __future__ import annotations

import threading
from typing import Dict

# Per-key locks. Entries persist for the process lifetime; memory cost is
# negligible (a few hundred bytes per unique key) compared to the
# computational savings of coalescing cold-miss stampedes.
_KEY_LOCKS: Dict[str, threading.Lock] = {}

# Protects _KEY_LOCKS itself against concurrent dict insertion. Held for
# microseconds — just long enough to look up or create the lock object.
_META_LOCK = threading.Lock()


def key_lock(key: str) -> threading.Lock:
    """Return the lock associated with ``key``, creating one if needed.

    Typical usage (double-checked locking around a cache):

        cached = cache_get(key)
        if cached is not None:
            return cached
        with key_lock(key):
            cached = cache_get(key)          # re-check under the lock
            if cached is not None:
                return cached
            value = expensive_build()        # only ONE thread runs this
            cache_put(key, value)
            return value

    The first thread to reach the `with` block acquires the lock and does
    the work. Threads 2..N block on acquire, then find the freshly-cached
    value on the re-check and skip the build.
    """
    with _META_LOCK:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _reset_for_tests() -> None:
    """Test-only helper — clear all per-key locks.

    Never call from production code; tests use this between cases to keep
    the lock registry from leaking across isolated scenarios.
    """
    with _META_LOCK:
        _KEY_LOCKS.clear()
