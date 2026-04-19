"""Tests for Phase 9.18 step 1 — per-file tree-sitter parse timeout.

Two layers:

* ``Test*Wrapper`` — exercises ``extract_definitions_with_timeout`` by
  stubbing out the subprocess pool (``get_parse_pool``) with an
  in-process fake. Tests the wrapper's control flow: skip-list
  pre-check, timeout → regex fallback, skip_fact writeback, env var
  parsing.
* ``TestParsePoolSubprocess`` — end-to-end tests that actually spawn a
  real subprocess worker and verify the SIGKILL + respawn path works.
  Slower (a few hundred ms each) but only place the real primitive is
  exercised.

The subprocess design replaces an earlier daemon-thread design that
was broken — py-spy on sentry-007 caught tree-sitter's C binding
holding the GIL through the parse, so in-process timeouts were dead
code. See ``parse_pool.py`` for the write-up.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.repo_graph.parser import (
    FileSymbols,
    _extract_with_regex,
    extract_definitions,
    extract_definitions_with_timeout,
)
from app.scratchpad import FactStore
from app.scratchpad.context import bind_factstore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    """Per-test FactStore rooted in tmp_path so ~/.conductor isn't touched."""
    monkeypatch.setattr("app.scratchpad.store.SCRATCHPAD_ROOT", tmp_path)
    store = FactStore.open(f"timeout-test-{uuid.uuid4().hex[:6]}", workspace="/ws")
    yield store
    store.delete()


class _FakePool:
    """Drop-in replacement for ParsePool. Configure ``returns`` before
    dispatching; inspect ``calls`` / ``last_call`` after."""

    def __init__(self) -> None:
        self.returns = None  # what parse() should return
        self.calls = 0
        self.last_call = None  # (file_path, timeout_s)

    def parse(self, source, language, file_path, timeout_s):
        self.calls += 1
        self.last_call = (file_path, timeout_s)
        return self.returns


@pytest.fixture
def fake_pool(monkeypatch):
    """Replace ``get_parse_pool()`` with a fake. Unit-level tests never
    spawn a real subprocess."""
    fake = _FakePool()
    monkeypatch.setattr(
        "app.repo_graph.parse_pool.get_parse_pool", lambda: fake
    )
    return fake


@pytest.fixture(autouse=True)
def _shutdown_any_real_pool():
    """Safety net: tear down any real pool spawned by a test. Keeps the
    test suite from leaking subprocesses if an assertion fails mid-test."""
    yield
    from app.repo_graph.parse_pool import shutdown_parse_pool
    shutdown_parse_pool()


# ---------------------------------------------------------------------------
# Wrapper-level: pool hit paths
# ---------------------------------------------------------------------------


class TestWrapperSuccessPath:
    def test_pool_returns_symbols_wrapper_passes_through(self, fake_pool, tmp_path):
        sentinel = FileSymbols(file_path="sentinel", language="python")
        fake_pool.returns = sentinel
        with patch("app.repo_graph.parser._extract_with_regex") as regex_mock:
            result = extract_definitions_with_timeout(
                str(tmp_path / "bar.py"),
                source=b"def bar(): pass\n",
                timeout_s=5.0,
            )
        assert result is sentinel
        assert fake_pool.calls == 1
        regex_mock.assert_not_called()

    def test_unknown_language_skips_pool(self, fake_pool, tmp_path):
        """When detect_language returns None, wrapper must not invoke
        the pool — regex fallback has nothing to do either."""
        result = extract_definitions_with_timeout(
            str(tmp_path / "unknown.xyz"),
            source=b"contents",
            timeout_s=5.0,
        )
        assert fake_pool.calls == 0
        assert result.definitions == []


class TestWrapperFallbackPath:
    def test_pool_timeout_falls_back_to_regex(self, fake_pool, tmp_path):
        """Pool returns None → wrapper must fall back to regex extractor
        and still return a usable FileSymbols."""
        fake_pool.returns = None
        result = extract_definitions_with_timeout(
            str(tmp_path / "hello.py"),
            source=b"def hello():\n    return 1\n",
            timeout_s=0.2,
        )
        assert any(d.name == "hello" for d in result.definitions)

    def test_pool_error_falls_back_to_regex(self, fake_pool, tmp_path):
        """Pool returns None for both timeouts AND worker errors; the
        wrapper cannot distinguish, and shouldn't need to."""
        fake_pool.returns = None
        result = extract_definitions_with_timeout(
            str(tmp_path / "foo.py"),
            source=b"def foo():\n    pass\n",
            timeout_s=1.0,
        )
        assert any(d.name == "foo" for d in result.definitions)


# ---------------------------------------------------------------------------
# Wrapper-level: skip_fact integration
# ---------------------------------------------------------------------------


class TestSkipFactsIntegration:
    def test_timeout_writes_skip_fact(self, fake_pool, isolated_vault, tmp_path):
        """When the pool signals failure and a vault is bound, the file
        must be recorded on the skip list so later calls bypass the pool."""
        fake_pool.returns = None
        with bind_factstore(isolated_vault):
            extract_definitions_with_timeout(
                str(tmp_path / "x.py"),
                source=b"def x(): pass\n",
                timeout_s=0.2,
            )
        reason = isolated_vault.should_skip(str(tmp_path / "x.py"))
        assert reason is not None
        assert "timeout" in reason.lower() or "failure" in reason.lower()

    def test_skip_fact_prevents_pool_dispatch(
        self, fake_pool, isolated_vault, tmp_path
    ):
        """Pre-check short-circuits — the pool must NOT be called when
        the file is on the skip list."""
        target = tmp_path / "y.py"
        isolated_vault.put_skip(str(target), reason="prior timeout", duration_ms=200)

        with bind_factstore(isolated_vault):
            result = extract_definitions_with_timeout(
                str(target), source=b"def y(): pass\n", timeout_s=5.0
            )
        assert fake_pool.calls == 0
        assert any(d.name == "y" for d in result.definitions)

    def test_no_vault_bound_still_works(self, fake_pool, tmp_path):
        """Scratchpad is optional — extraction works when nothing's bound."""
        fake_pool.returns = None
        result = extract_definitions_with_timeout(
            str(tmp_path / "z.py"),
            source=b"def z(): pass\n",
            timeout_s=0.2,
        )
        assert any(d.name == "z" for d in result.definitions)

    def test_vault_put_skip_error_does_not_propagate(
        self, fake_pool, isolated_vault, tmp_path
    ):
        """A broken vault must not break extraction — caching is always
        best-effort."""
        fake_pool.returns = None
        with bind_factstore(isolated_vault), patch.object(
            isolated_vault, "put_skip", side_effect=RuntimeError("disk full")
        ):
            result = extract_definitions_with_timeout(
                str(tmp_path / "w.py"),
                source=b"def w(): pass\n",
                timeout_s=0.2,
            )
        assert any(d.name == "w" for d in result.definitions)


# ---------------------------------------------------------------------------
# Wrapper-level: timeout parsing + disabled mode
# ---------------------------------------------------------------------------


class TestTimeoutConfig:
    def test_zero_timeout_bypasses_pool(self, fake_pool, tmp_path):
        """``timeout_s=0`` is explicit opt-out — pool not invoked,
        tree-sitter runs in-process. Useful for tests and benchmarks
        that must not spawn a subprocess."""
        sentinel = FileSymbols(file_path="s", language="python")
        with patch("app.repo_graph.parser._extract_with_tree_sitter", lambda *a: sentinel):
            result = extract_definitions_with_timeout(
                str(tmp_path / "s.py"), source=b"pass\n", timeout_s=0
            )
        assert result is sentinel
        assert fake_pool.calls == 0

    def test_env_var_drives_default_timeout(self, fake_pool, tmp_path, monkeypatch):
        monkeypatch.setenv("CONDUCTOR_PARSE_TIMEOUT_S", "0.1")
        fake_pool.returns = None
        extract_definitions_with_timeout(
            str(tmp_path / "q.py"),
            source=b"def q(): pass\n",
            timeout_s=None,
        )
        assert fake_pool.last_call[1] == 0.1

    def test_invalid_env_var_falls_back_to_60s_default(
        self, fake_pool, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("CONDUCTOR_PARSE_TIMEOUT_S", "not-a-number")
        sentinel = FileSymbols(file_path="n", language="python")
        fake_pool.returns = sentinel
        result = extract_definitions_with_timeout(
            str(tmp_path / "n.py"), source=b"pass\n"
        )
        assert result is sentinel
        assert fake_pool.last_call[1] == 60.0


# ---------------------------------------------------------------------------
# Public API: extract_definitions now delegates to the timed wrapper
# ---------------------------------------------------------------------------


class TestExtractDefinitionsDelegation:
    def test_extract_definitions_routes_through_pool(self, fake_pool, tmp_path):
        """Every call site in the codebase that uses ``extract_definitions``
        gets the subprocess ceiling for free."""
        fake_pool.returns = None
        result = extract_definitions(
            str(tmp_path / "a.py"),
            source=b"def a(): pass\n",
            timeout_s=0.2,
        )
        assert any(d.name == "a" for d in result.definitions)
        assert fake_pool.calls == 1

    def test_regex_fallback_is_deterministic(self, tmp_path):
        """Regex fallback is pure Python — same input always yields same
        output, no subprocess involvement."""
        src = b"def direct_fallback(): pass\n"
        result = _extract_with_regex(src.decode("utf-8"), "python", str(tmp_path / "d.py"))
        assert any(d.name == "direct_fallback" for d in result.definitions)


# ---------------------------------------------------------------------------
# End-to-end: real subprocess exercises
# ---------------------------------------------------------------------------


class TestParsePoolSubprocess:
    """These tests spawn actual subprocesses. Slow (~300-800ms each for
    spawn overhead) but the only place we verify the primitive works."""

    def test_pool_parses_simple_python(self):
        from app.repo_graph.parse_pool import ParsePool

        pool = ParsePool()
        try:
            result = pool.parse(
                b"def hello():\n    return 1\n",
                "python",
                "test.py",
                timeout_s=30.0,
            )
        finally:
            pool.shutdown()
        assert result is not None
        assert any(d.name == "hello" for d in result.definitions)

    def test_pool_survives_after_timeout_and_respawns(self):
        """Force a timeout so short that the worker can't complete, then
        verify the pool spawns a fresh worker for the next parse."""
        from app.repo_graph.parse_pool import ParsePool

        pool = ParsePool()
        try:
            # 0s timeout — the poll returns immediately without a result.
            # The worker is killed + respawned. We don't assert on the
            # first parse's outcome (could be anything); we assert the
            # pool remains usable.
            r1 = pool.parse(
                b"def a(): pass\n" * 1000,
                "python",
                "big.py",
                timeout_s=0.001,
            )
            # Next parse must succeed on a fresh worker.
            r2 = pool.parse(
                b"def z(): return 0\n",
                "python",
                "z.py",
                timeout_s=30.0,
            )
        finally:
            pool.shutdown()
        assert r2 is not None
        assert any(d.name == "z" for d in r2.definitions)
        # r1 is None (timeout) by design.
        assert r1 is None

    def test_shutdown_is_idempotent(self):
        from app.repo_graph.parse_pool import ParsePool

        pool = ParsePool()
        pool.shutdown()
        pool.shutdown()  # must not raise
