"""Scratchpad CLI tests — Phase 9.15 full INDEX dump."""

from __future__ import annotations

import uuid

import pytest

from app.scratchpad import FactStore
from app.scratchpad.__main__ import main


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Per-test session DB, with the CLI's SCRATCHPAD_ROOT redirected to
    tmp_path so we don't touch the developer's real ~/.conductor."""
    monkeypatch.setattr("app.scratchpad.store.SCRATCHPAD_ROOT", tmp_path)
    monkeypatch.setattr("app.scratchpad.__main__.SCRATCHPAD_ROOT", tmp_path)
    session_id = f"cli-{uuid.uuid4().hex[:8]}"
    s = FactStore.open(session_id, workspace="/fake/ws")
    yield session_id, s, tmp_path
    s.delete()


class TestListCommand:
    def test_empty_scratchpad(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("app.scratchpad.__main__.SCRATCHPAD_ROOT", tmp_path)
        assert main(["list"]) == 0
        out = capsys.readouterr().out
        assert "no session DBs" in out

    def test_missing_root(self, tmp_path, monkeypatch, capsys):
        missing = tmp_path / "does-not-exist"
        monkeypatch.setattr("app.scratchpad.__main__.SCRATCHPAD_ROOT", missing)
        assert main(["list"]) == 0
        err = capsys.readouterr().err
        assert "No scratchpad directory" in err

    def test_lists_session_with_stats(self, session, capsys):
        session_id, store, _ = session
        store.put("k1", tool="grep", content=[])
        store.put("k2", tool="read_file", content="")
        store.put_negative("n1", tool="find_symbol", query="X")

        assert main(["list"]) == 0
        out = capsys.readouterr().out
        assert session_id in out
        assert "facts=2" in out
        assert "negative_facts=1" in out


class TestDumpCommand:
    def test_missing_session_returns_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("app.scratchpad.__main__.SCRATCHPAD_ROOT", tmp_path)
        assert main(["dump", "does-not-exist"]) == 1
        err = capsys.readouterr().err
        assert "Session not found" in err

    def test_dump_renders_markdown(self, session, capsys):
        session_id, store, _ = session
        store.put(
            "v1:grep:foo::py::",
            tool="grep",
            content=[{"file": "a.py", "line": 10}],
            path="/ws",
            agent="correctness",
        )
        store.put(
            "v1:read_file:/abs/x.py:100:150",
            tool="read_file",
            content="…",
            path="/abs/x.py",
            range_start=100,
            range_end=150,
            agent="security",
        )
        store.put_negative(
            "v1:find_symbol:GhostClass:",
            tool="find_symbol",
            query="GhostClass",
            reason="not defined",
            confidence=0.95,
        )
        store.put_skip("/abs/pathological.tsx", reason="parse timeout 30s", duration_ms=30000)

        assert main(["dump", session_id]) == 0
        out = capsys.readouterr().out
        assert f"session {session_id}" in out
        assert "## Summary" in out
        assert "facts: 2" in out
        assert "negative_facts: 1" in out
        assert "skip_facts: 1" in out
        # Recent facts table
        assert "## Recent facts" in out
        assert "| grep |" in out
        assert "| read_file |" in out
        assert "100-150" in out
        # By-tool breakdown
        assert "## Facts by tool" in out
        # Negative facts
        assert "## Negative facts" in out
        assert "GhostClass" in out
        # Skip facts
        assert "## Skipped files" in out
        assert "/abs/pathological.tsx" in out


class TestSweepCommand:
    def test_sweep_no_orphans(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("app.scratchpad.store.SCRATCHPAD_ROOT", tmp_path)
        monkeypatch.setattr("app.scratchpad.__main__.SCRATCHPAD_ROOT", tmp_path)
        assert main(["sweep"]) == 0
        out = capsys.readouterr().out
        assert "No sessions older than 24h" in out

    def test_sweep_removes_stale(self, tmp_path, monkeypatch, capsys):
        import os
        import time

        monkeypatch.setattr("app.scratchpad.store.SCRATCHPAD_ROOT", tmp_path)
        monkeypatch.setattr("app.scratchpad.__main__.SCRATCHPAD_ROOT", tmp_path)

        stale = tmp_path / "stale.sqlite"
        stale.write_bytes(b"SQLite format 3\x00")
        old = time.time() - (48 * 3600)
        os.utime(stale, (old, old))

        assert main(["sweep", "--hours", "24"]) == 0
        out = capsys.readouterr().out
        assert "Removed 1 session" in out
        assert not stale.exists()
