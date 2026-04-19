"""Canonical key builder tests — Phase 9.15 full."""

from __future__ import annotations

from app.scratchpad.keys import (
    CACHEABLE_TOOLS,
    RANGE_TOOLS,
    SCHEMA_VERSION,
    build_key,
    extract_path,
    extract_range,
)


class TestBuildKey:
    def test_grep_deterministic(self):
        """Same params → same key, regardless of caller."""
        p = {"pattern": "foo", "path": "/abs/src", "context_lines": 3}
        assert build_key("grep", p) == build_key("grep", p)

    def test_grep_whitespace_normalised(self):
        """Leading/trailing whitespace in pattern doesn't split the cache."""
        a = build_key("grep", {"pattern": "foo", "path": "/abs/src"})
        b = build_key("grep", {"pattern": " foo ", "path": "/abs/src"})
        assert a == b

    def test_grep_glob_sorted(self):
        """Glob order doesn't change the key."""
        a = build_key("grep", {"pattern": "x", "path": "/src", "glob": ["*.py", "*.md"]})
        b = build_key("grep", {"pattern": "x", "path": "/src", "glob": ["*.md", "*.py"]})
        assert a == b

    def test_grep_case_insensitive_affects_key(self):
        """Different modifiers → different results → different keys."""
        a = build_key("grep", {"pattern": "foo", "path": "/src"})
        b = build_key("grep", {"pattern": "foo", "path": "/src", "case_insensitive": True})
        assert a != b

    def test_read_file_with_range(self):
        k = build_key("read_file", {"path": "/abs/x.py", "start_line": 10, "end_line": 50})
        assert k is not None
        assert "read_file" in k
        assert ":10:50" in k

    def test_read_file_alias_file_path(self):
        """`file_path` should canonicalise to the same key as `path`."""
        a = build_key("read_file", {"path": "/abs/x.py", "start_line": 1, "end_line": 10})
        b = build_key("read_file", {"file_path": "/abs/x.py", "start_line": 1, "end_line": 10})
        assert a == b

    def test_find_symbol_exact(self):
        k = build_key("find_symbol", {"symbol": "MyClass", "path_prefix": "/src"})
        assert k is not None
        assert "find_symbol" in k
        assert "MyClass" in k

    def test_non_cacheable_returns_none(self):
        """Tools that mutate state or hit external services MUST NOT be cached."""
        assert build_key("file_edit", {"path": "/x.py", "content": "…"}) is None
        assert build_key("file_write", {"path": "/x.py", "content": "…"}) is None
        assert build_key("run_test", {"path": "/tests"}) is None
        assert build_key("web_search", {"query": "python"}) is None
        assert build_key("web_navigate", {"url": "https://example.com"}) is None

    def test_version_prefix(self):
        k = build_key("grep", {"pattern": "x", "path": "/a"})
        assert k.startswith(f"{SCHEMA_VERSION}:")

    def test_different_tools_distinct_keys(self):
        p = {"path": "/abs/x.py"}
        a = build_key("file_outline", p)
        b = build_key("get_dependencies", p)
        assert a is not None and b is not None and a != b


class TestExtractPath:
    def test_read_file(self):
        p = extract_path("read_file", {"path": "/abs/x.py", "start_line": 1, "end_line": 5})
        assert p is not None
        assert p.endswith("x.py")

    def test_find_symbol_uses_path_prefix(self):
        p = extract_path("find_symbol", {"symbol": "foo", "path_prefix": "/abs/src"})
        assert p is not None and p.endswith("src")

    def test_non_path_tool(self):
        assert extract_path("grep", {"pattern": "x"}) is None


class TestExtractRange:
    def test_read_file(self):
        assert extract_range("read_file", {"start_line": 10, "end_line": 20}) == (10, 20)

    def test_git_blame(self):
        assert extract_range("git_blame", {"start_line": 5, "end_line": 15}) == (5, 15)

    def test_non_range_tool(self):
        assert extract_range("grep", {"start_line": 10}) == (None, None)

    def test_missing_range(self):
        assert extract_range("read_file", {}) == (None, None)

    def test_string_coerced_to_int(self):
        assert extract_range("read_file", {"start_line": "10", "end_line": "20"}) == (10, 20)

    def test_garbled_range_returns_none(self):
        """Pre-bracket-repair garbled input should yield None, not crash."""
        assert extract_range("read_file", {"start_line": "[10", "end_line": "20]"}) == (None, None)


class TestTableInvariants:
    def test_range_tools_subset_of_cacheable(self):
        assert RANGE_TOOLS.issubset(CACHEABLE_TOOLS)

    def test_mutating_tools_not_cacheable(self):
        """Sanity: if these ever land in CACHEABLE_TOOLS we have a bug."""
        for tool in ("file_edit", "file_write", "run_test"):
            assert tool not in CACHEABLE_TOOLS
