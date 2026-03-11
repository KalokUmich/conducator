"""Tests for code intelligence tools."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Dict

import pytest

from app.code_tools.tools import (
    ast_search,
    execute_tool,
    file_outline,
    find_references,
    find_symbol,
    get_callers,
    get_callees,
    get_dependencies,
    get_dependents,
    git_diff,
    git_log,
    grep,
    invalidate_graph_cache,
    list_files,
    read_file,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace with source files."""
    # Python file
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "main.py").write_text(textwrap.dedent("""\
        from app.service import MyService

        class App:
            def __init__(self):
                self.service = MyService()

            def run(self):
                return self.service.process()
    """))
    (tmp_path / "app" / "service.py").write_text(textwrap.dedent("""\
        from app.utils import helper

        class MyService:
            def process(self):
                return helper("data")

        def standalone_function():
            pass

        def orchestrate():
            result = helper("input")
            standalone_function()
            return result
    """))
    (tmp_path / "app" / "utils.py").write_text(textwrap.dedent("""\
        def helper(data: str) -> str:
            return data.upper()

        def unused_helper():
            return 42
    """))

    # TypeScript file
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(textwrap.dedent("""\
        import { greet } from './utils';

        function main(): void {
            console.log(greet("world"));
        }

        export class Application {
            start() {
                main();
            }
        }
    """))
    (tmp_path / "src" / "utils.ts").write_text(textwrap.dedent("""\
        export function greet(name: string): string {
            return `Hello, ${name}!`;
        }
    """))

    # node_modules (should be excluded)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("module.exports = {}")

    # Large file (should be skipped in search)
    (tmp_path / "large.py").write_text("x = 1\n" * 200_000)

    invalidate_graph_cache()
    return tmp_path


@pytest.fixture()
def ws(workspace: Path) -> str:
    return str(workspace)


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


class TestGrep:
    def test_basic_pattern(self, ws):
        result = grep(ws, "MyService")
        assert result.success
        assert len(result.data) > 0
        paths = {m["file_path"] for m in result.data}
        assert "app/service.py" in paths

    def test_regex_pattern(self, ws):
        result = grep(ws, r"def\s+\w+\(")
        assert result.success
        assert len(result.data) >= 3  # helper, unused_helper, standalone_function, process

    def test_include_glob(self, ws):
        result = grep(ws, "function", include_glob="*.ts")
        assert result.success
        for m in result.data:
            assert m["file_path"].endswith(".ts")

    def test_path_filter(self, ws):
        result = grep(ws, "class", path="app")
        assert result.success
        for m in result.data:
            assert m["file_path"].startswith("app/")

    def test_excludes_node_modules(self, ws):
        result = grep(ws, "module.exports")
        assert result.success
        assert len(result.data) == 0

    def test_max_results(self, ws):
        result = grep(ws, r"\w+", max_results=5)
        assert result.success
        assert len(result.data) <= 5
        assert result.truncated

    def test_invalid_regex(self, ws):
        result = grep(ws, "[invalid")
        assert not result.success
        assert "Invalid regex" in result.error

    def test_nonexistent_path(self, ws):
        result = grep(ws, "test", path="nonexistent")
        assert not result.success

    def test_path_traversal_blocked(self, ws):
        with pytest.raises(ValueError, match="escapes workspace"):
            grep(ws, "test", path="../../etc/passwd")


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    def test_read_full_file(self, ws):
        result = read_file(ws, "app/utils.py")
        assert result.success
        assert "helper" in result.data["content"]
        assert result.data["total_lines"] > 0

    def test_read_line_range(self, ws):
        result = read_file(ws, "app/service.py", start_line=3, end_line=5)
        assert result.success
        assert "MyService" in result.data["content"]

    def test_nonexistent_file(self, ws):
        result = read_file(ws, "nonexistent.py")
        assert not result.success

    def test_line_numbers_in_output(self, ws):
        result = read_file(ws, "app/utils.py")
        assert result.success
        assert "   1 |" in result.data["content"]


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


class TestListFiles:
    def test_list_root(self, ws):
        result = list_files(ws)
        assert result.success
        paths = {e["path"] for e in result.data}
        assert "app" in paths or any("app" in p for p in paths)

    def test_list_subdirectory(self, ws):
        result = list_files(ws, directory="app")
        assert result.success
        paths = {e["path"] for e in result.data}
        assert any("main.py" in p for p in paths)

    def test_max_depth(self, ws):
        result = list_files(ws, max_depth=1)
        assert result.success
        # Should not recurse into subdirectories
        for entry in result.data:
            parts = Path(entry["path"]).parts
            assert len(parts) <= 2

    def test_include_glob(self, ws):
        result = list_files(ws, include_glob="*.py")
        assert result.success
        for entry in result.data:
            if not entry["is_dir"]:
                assert entry["path"].endswith(".py")

    def test_excludes_node_modules(self, ws):
        result = list_files(ws, max_depth=5)
        assert result.success
        paths = {e["path"] for e in result.data}
        assert not any("node_modules" in p for p in paths)

    def test_nonexistent_dir(self, ws):
        result = list_files(ws, directory="nonexistent")
        assert not result.success


# ---------------------------------------------------------------------------
# find_symbol
# ---------------------------------------------------------------------------


class TestFindSymbol:
    def test_find_class(self, ws):
        result = find_symbol(ws, "MyService")
        assert result.success
        assert len(result.data) >= 1
        sym = result.data[0]
        assert sym["name"] == "MyService"
        assert sym["kind"] == "class"
        assert sym["file_path"] == "app/service.py"

    def test_find_function(self, ws):
        result = find_symbol(ws, "helper")
        assert result.success
        assert len(result.data) >= 1
        names = {s["name"] for s in result.data}
        assert "helper" in names

    def test_substring_match(self, ws):
        result = find_symbol(ws, "helper")
        assert result.success
        names = {s["name"] for s in result.data}
        assert "unused_helper" in names

    def test_kind_filter(self, ws):
        result = find_symbol(ws, "MyService", kind="class")
        assert result.success
        assert all(s["kind"] == "class" for s in result.data)

    def test_kind_filter_no_match(self, ws):
        result = find_symbol(ws, "MyService", kind="function")
        assert result.success
        assert len(result.data) == 0

    def test_not_found(self, ws):
        result = find_symbol(ws, "NonExistentSymbol12345")
        assert result.success
        assert len(result.data) == 0


# ---------------------------------------------------------------------------
# find_references
# ---------------------------------------------------------------------------


class TestFindReferences:
    def test_find_refs(self, ws):
        result = find_references(ws, "helper")
        assert result.success
        assert len(result.data) >= 1
        files = {r["file_path"] for r in result.data}
        assert "app/service.py" in files

    def test_find_refs_in_file(self, ws):
        result = find_references(ws, "MyService", file="app/main.py")
        assert result.success
        for r in result.data:
            assert r["file_path"] == "app/main.py"


# ---------------------------------------------------------------------------
# file_outline
# ---------------------------------------------------------------------------


class TestFileOutline:
    def test_python_outline(self, ws):
        result = file_outline(ws, "app/service.py")
        assert result.success
        names = {d["name"] for d in result.data}
        assert "MyService" in names
        assert "standalone_function" in names

    def test_nonexistent_file(self, ws):
        result = file_outline(ws, "nonexistent.py")
        assert not result.success


# ---------------------------------------------------------------------------
# ast_search
# ---------------------------------------------------------------------------


class TestAstSearch:
    def test_basic_pattern(self, ws):
        result = ast_search(ws, "def $F($$$ARGS)", language="python")
        assert result.success
        assert len(result.data) >= 3  # helper, unused_helper, standalone_function, etc.
        # Check structure
        for m in result.data:
            assert "file_path" in m
            assert "start_line" in m
            assert "text" in m

    def test_meta_variables(self, ws):
        result = ast_search(ws, "def $F($$$ARGS)", language="python")
        assert result.success
        # At least one match should have $F captured
        has_meta = any(m.get("meta_variables", {}).get("$F") for m in result.data)
        assert has_meta

    def test_path_filter(self, ws):
        result = ast_search(ws, "class $C", language="python", path="app")
        assert result.success
        for m in result.data:
            assert m["file_path"].startswith("app/")

    def test_typescript_pattern(self, ws):
        # TS return type annotations break ($$$ARGS) matching; use broader pattern
        result = ast_search(ws, "function $NAME", language="typescript", path="src")
        assert result.success
        assert len(result.data) >= 1
        names = {m.get("meta_variables", {}).get("$NAME", "") for m in result.data}
        assert "main" in names or "greet" in names

    def test_max_results(self, ws):
        result = ast_search(ws, "$X", language="python", max_results=2)
        assert result.success
        assert len(result.data) <= 2

    def test_nonexistent_path(self, ws):
        result = ast_search(ws, "def $F()", path="nonexistent")
        assert not result.success

    def test_excludes_node_modules(self, ws):
        result = ast_search(ws, "module", language="javascript")
        assert result.success
        for m in result.data:
            assert "node_modules" not in m["file_path"]


# ---------------------------------------------------------------------------
# get_callees / get_callers
# ---------------------------------------------------------------------------


class TestGetCallees:
    def test_find_callees(self, ws):
        result = get_callees(ws, "orchestrate", file="app/service.py")
        assert result.success
        callee_names = {c["callee_name"] for c in result.data}
        assert "helper" in callee_names
        assert "standalone_function" in callee_names

    def test_function_not_found(self, ws):
        result = get_callees(ws, "nonexistent_fn", file="app/service.py")
        assert not result.success
        assert "not found" in result.error

    def test_file_not_found(self, ws):
        result = get_callees(ws, "orchestrate", file="nonexistent.py")
        assert not result.success

    def test_no_callees(self, ws):
        result = get_callees(ws, "unused_helper", file="app/utils.py")
        assert result.success
        assert len(result.data) == 0


class TestGetCallers:
    def test_find_callers(self, ws):
        result = get_callers(ws, "helper")
        assert result.success
        assert len(result.data) >= 1
        caller_names = {c["caller_name"] for c in result.data}
        assert "orchestrate" in caller_names

    def test_find_callers_path_filter(self, ws):
        result = get_callers(ws, "helper", path="app")
        assert result.success
        for c in result.data:
            assert c["file_path"].startswith("app/")

    def test_no_callers(self, ws):
        result = get_callers(ws, "unused_helper")
        assert result.success
        assert len(result.data) == 0

    def test_nonexistent_path(self, ws):
        result = get_callers(ws, "helper", path="nonexistent")
        assert not result.success


# ---------------------------------------------------------------------------
# get_dependencies / get_dependents
# ---------------------------------------------------------------------------


class TestGraphTools:
    def test_get_dependencies(self, ws):
        result = get_dependencies(ws, "app/main.py")
        assert result.success
        # main.py imports from service.py
        dep_files = {d["file_path"] for d in result.data}
        assert "app/service.py" in dep_files

    def test_get_dependents(self, ws):
        result = get_dependents(ws, "app/service.py")
        assert result.success
        # main.py depends on service.py
        dep_files = {d["file_path"] for d in result.data}
        assert "app/main.py" in dep_files


# ---------------------------------------------------------------------------
# git_log / git_diff
# ---------------------------------------------------------------------------


class TestGitTools:
    @pytest.fixture(autouse=True)
    def _init_git(self, workspace):
        """Initialize a git repo in the workspace."""
        os.system(f"cd {workspace} && git init -q && git add -A && git commit -q -m 'init'")

    def test_git_log(self, ws):
        result = git_log(ws)
        assert result.success
        assert len(result.data) >= 1
        assert result.data[0]["message"] == "init"

    def test_git_log_file(self, ws):
        result = git_log(ws, file="app/main.py")
        assert result.success
        assert len(result.data) >= 1

    def test_git_diff(self, ws):
        # No diff on clean repo
        result = git_diff(ws, ref1="HEAD~1", ref2="HEAD")
        assert result.success


# ---------------------------------------------------------------------------
# execute_tool dispatcher
# ---------------------------------------------------------------------------


class TestExecuteTool:
    def test_dispatch_grep(self, ws):
        result = execute_tool("grep", ws, {"pattern": "class"})
        assert result.success

    def test_dispatch_unknown(self, ws):
        result = execute_tool("unknown_tool", ws, {})
        assert not result.success
        assert "Unknown tool" in result.error

    def test_dispatch_bad_params(self, ws):
        result = execute_tool("grep", ws, {"bad_param": True})
        assert not result.success
