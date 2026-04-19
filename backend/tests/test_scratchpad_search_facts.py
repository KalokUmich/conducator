"""search_facts tool tests — Phase 9.15 full."""

from __future__ import annotations

import uuid

import pytest

from app.code_tools.tools import execute_tool, search_facts
from app.scratchpad import FactStore, bind_factstore


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.scratchpad.store.SCRATCHPAD_ROOT", tmp_path)
    session_id = f"sf-{uuid.uuid4().hex[:8]}"
    s = FactStore.open(session_id, workspace="/fake/ws")
    yield s
    s.delete()


class TestSearchFactsDirect:
    def test_no_active_session_returns_error(self):
        """Outside a PR review (no bound store) — friendly error, not crash."""
        result = search_facts(workspace="/fake/ws")
        assert result.success is False
        assert "No active scratchpad session" in result.error

    def test_empty_store_returns_zero(self, session):
        with bind_factstore(session):
            result = search_facts(workspace="/fake/ws")
        assert result.success
        assert result.data["count"] == 0
        assert result.data["facts"] == []

    def test_returns_fact_headers_not_content(self, session):
        session.put(
            key="v1:grep:foo::py::",
            tool="grep",
            content=[{"file": "a.py", "line": 10, "text": "match"}] * 100,  # lots of content
            path="/ws/src",
            agent="correctness",
        )
        with bind_factstore(session):
            result = search_facts(workspace="/fake/ws")

        assert result.success
        assert result.data["count"] == 1
        fact = result.data["facts"][0]
        # Headers present
        assert fact["key"] == "v1:grep:foo::py::"
        assert fact["tool"] == "grep"
        assert fact["path"] == "/ws/src"
        assert fact["agent"] == "correctness"
        # Content NOT inlined — caller re-runs original tool for content
        assert "content" not in fact

    def test_filter_by_tool(self, session):
        session.put("k1", tool="grep", content=[])
        session.put("k2", tool="read_file", content="")
        session.put("k3", tool="grep", content=[])

        with bind_factstore(session):
            result = search_facts(workspace="/fake/ws", tool="grep")

        assert result.success
        assert result.data["count"] == 2
        assert all(f["tool"] == "grep" for f in result.data["facts"])

    def test_filter_by_path_substring(self, session):
        session.put("k1", tool="read_file", content="", path="/abs/src/auth.py")
        session.put("k2", tool="read_file", content="", path="/abs/src/payment.py")
        session.put("k3", tool="read_file", content="", path="/abs/tests/test_auth.py")

        with bind_factstore(session):
            result = search_facts(workspace="/fake/ws", path="auth")

        assert result.success
        # "auth" matches both auth.py and test_auth.py
        assert result.data["count"] == 2
        paths = {f["path"] for f in result.data["facts"]}
        assert paths == {"/abs/src/auth.py", "/abs/tests/test_auth.py"}

    def test_filter_by_pattern_in_key(self, session):
        session.put("v1:grep:authenticate::py::", tool="grep", content=[])
        session.put("v1:grep:logout::py::", tool="grep", content=[])
        session.put("v1:grep:authorize::py::", tool="grep", content=[])

        with bind_factstore(session):
            result = search_facts(workspace="/fake/ws", pattern="auth")

        assert result.success
        # matches authenticate + authorize; logout excluded
        keys = {f["key"] for f in result.data["facts"]}
        assert keys == {"v1:grep:authenticate::py::", "v1:grep:authorize::py::"}

    def test_limit_clamps(self, session):
        for i in range(50):
            session.put(f"k{i}", tool="grep", content=[])

        with bind_factstore(session):
            result = search_facts(workspace="/fake/ws", limit=10)

        assert result.success
        assert result.data["count"] == 10

    def test_combined_filters(self, session):
        session.put("v1:grep:auth::py::", tool="grep", content=[], path="/src/auth.py")
        session.put("v1:grep:auth::ts::", tool="grep", content=[], path="/src/auth.ts")
        session.put("v1:grep:logout::py::", tool="grep", content=[], path="/src/auth.py")

        with bind_factstore(session):
            result = search_facts(workspace="/fake/ws", tool="grep", path="auth.py", pattern="auth")

        assert result.success
        # Only the first one matches all three filters
        assert result.data["count"] == 1
        assert result.data["facts"][0]["path"] == "/src/auth.py"
        assert "auth" in result.data["facts"][0]["key"]

    def test_stats_included(self, session):
        session.put("k1", tool="grep", content=[])
        session.put_negative("n1", tool="find_symbol", query="X")

        with bind_factstore(session):
            result = search_facts(workspace="/fake/ws")

        assert result.success
        stats = result.data["stats"]
        assert stats["facts"] == 1
        assert stats["negative_facts"] == 1


class TestDispatchThroughExecuteTool:
    """Verify the tool is discoverable via execute_tool's dispatcher."""

    def test_execute_tool_routes_to_search_facts(self, session, tmp_path):
        session.put("k1", tool="grep", content=[], path="/ws/x.py")

        with bind_factstore(session):
            result = execute_tool(
                "search_facts",
                str(tmp_path),
                {"tool": "grep"},
            )

        assert result.success
        assert result.data["count"] == 1

    def test_pydantic_validates_limit(self, session, tmp_path):
        """SearchFactsParams clamps limit to [1, 100]; invalid values rejected."""
        with bind_factstore(session):
            # limit=500 should fail validation before the tool runs
            result = execute_tool(
                "search_facts",
                str(tmp_path),
                {"limit": 500},
            )
        # Pydantic rejects via the param model; we see a validation error
        assert result.success is False
        assert "Invalid parameters" in (result.error or "") or "limit" in (result.error or "").lower()
