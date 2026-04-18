"""Tests for scratchpad in-flight dedup (Phase 9.15 MVP)."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.scratchpad import key_lock
from app.scratchpad.inflight import _reset_for_tests


@pytest.fixture(autouse=True)
def _reset():
    _reset_for_tests()
    yield
    _reset_for_tests()


class TestKeyLock:
    def test_same_key_returns_same_lock(self):
        """Two callers with the same key get the same Lock object."""
        a = key_lock("my-key")
        b = key_lock("my-key")
        assert a is b

    def test_different_keys_get_different_locks(self):
        """Different keys don't serialise against each other."""
        a = key_lock("workspace-1")
        b = key_lock("workspace-2")
        assert a is not b

    def test_concurrent_cold_miss_coalesces_to_one_build(self):
        """Core behaviour: N threads cold-miss the same key; exactly ONE
        runs the expensive work, the others block then see the cached value.

        Models the sentry-006 bug: 7 parallel sub-agents would otherwise
        all rebuild the same dependency graph.
        """
        cache: dict[str, int] = {}
        build_count = 0
        build_count_lock = threading.Lock()

        def get_or_build(key: str) -> int:
            # Fast path
            if key in cache:
                return cache[key]

            # Slow path with per-key lock
            with key_lock(key):
                # Re-check under lock (double-checked locking)
                if key in cache:
                    return cache[key]

                nonlocal build_count
                with build_count_lock:
                    build_count += 1

                # Simulate expensive work (long enough that other threads
                # will definitely be waiting on the lock)
                time.sleep(0.1)
                cache[key] = 42
                return cache[key]

        # Fire 7 threads concurrently on the same key
        with ThreadPoolExecutor(max_workers=7) as pool:
            futures = [pool.submit(get_or_build, "shared-key") for _ in range(7)]
            results = [f.result() for f in futures]

        assert results == [42] * 7
        # CRITICAL: only ONE thread actually built; the other 6 returned
        # the cached value after blocking briefly.
        assert build_count == 1

    def test_different_keys_build_in_parallel(self):
        """Two threads on different keys should NOT serialise —
        build_dependency_graph for workspace-A and workspace-B can run
        concurrently (different lock objects).
        """
        cache: dict[str, int] = {}
        barrier = threading.Barrier(2)
        both_reached_build = threading.Event()

        def get_or_build(key: str) -> int:
            if key in cache:
                return cache[key]
            with key_lock(key):
                if key in cache:
                    return cache[key]
                # Both threads should reach here at the same time
                # (different keys, so they acquire different locks).
                barrier.wait(timeout=2.0)  # raises BrokenBarrierError if serialised
                both_reached_build.set()
                cache[key] = len(key)
                return cache[key]

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(get_or_build, "workspace-a")
            f2 = pool.submit(get_or_build, "workspace-b")
            assert f1.result() == len("workspace-a")
            assert f2.result() == len("workspace-b")

        assert both_reached_build.is_set()

    def test_exception_in_leader_does_not_deadlock_followers(self):
        """If the leader raises, followers must not block forever.

        The lock context manager releases on exception, so followers
        acquire next. They re-check the cache, see no entry, and
        (in real code) retry the build themselves.
        """
        cache: dict[str, int] = {}
        call_count = 0
        call_count_lock = threading.Lock()
        first_call_done = threading.Event()

        def get_or_build(key: str) -> int:
            if key in cache:
                return cache[key]
            with key_lock(key):
                if key in cache:
                    return cache[key]
                nonlocal call_count
                with call_count_lock:
                    call_count += 1
                    is_first = call_count == 1
                if is_first:
                    first_call_done.set()
                    raise RuntimeError("leader failed")
                # Follower succeeds
                cache[key] = 99
                return cache[key]

        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(get_or_build, "flaky")
            # Make sure leader starts first
            first_call_done.wait(timeout=1.0)
            follower = pool.submit(get_or_build, "flaky")

            with pytest.raises(RuntimeError, match="leader failed"):
                leader.result()
            # Follower should not deadlock; it retries and succeeds
            assert follower.result() == 99

        assert call_count == 2  # leader + follower retry


class TestEnsureGraphIntegration:
    """Integration: _ensure_graph now uses key_lock — verify coalescing."""

    def test_ensure_graph_coalesces_concurrent_builders(self, monkeypatch, tmp_path):
        """7 threads on the same workspace → 1 real build_dependency_graph call."""
        from app.code_tools import tools

        # Clear the module-level graph cache so we start cold
        tools.invalidate_graph_cache()

        # Force a fresh lock registry for a clean test
        _reset_for_tests()

        build_count = 0
        build_count_lock = threading.Lock()

        class StubGraphService:
            def build_graph(self, workspace: str):
                nonlocal build_count
                with build_count_lock:
                    build_count += 1
                # Simulate expensive build
                time.sleep(0.1)
                return {"workspace": workspace, "built_by": threading.get_ident()}

        workspace = str(tmp_path)
        service = StubGraphService()

        def call_it():
            return tools._ensure_graph(workspace, graph_service=service)

        with ThreadPoolExecutor(max_workers=7) as pool:
            futures = [pool.submit(call_it) for _ in range(7)]
            results = [f.result() for f in futures]

        # All 7 got the same graph object (cached)
        assert len({id(r) for r in results}) == 1
        # And only one actually ran build_graph
        assert build_count == 1

        # Cleanup — don't leak cache to other tests
        tools.invalidate_graph_cache(workspace)
