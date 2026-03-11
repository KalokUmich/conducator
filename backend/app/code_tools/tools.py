"""Code intelligence tool implementations.

Each tool operates within a *workspace_path* sandbox. All file paths
accepted and returned are **relative** to the workspace root.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import (
    AstMatch,
    CalleeInfo,
    CallerInfo,
    DependencyInfo,
    FileEntry,
    GitCommit,
    GrepMatch,
    ReferenceLocation,
    SymbolLocation,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Directories to always exclude from traversal
_EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "target",
    "dist", "vendor", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".tox", "build", ".next", ".nuxt",
}

_MAX_FILE_SIZE = 512_000  # 500 KB — skip larger files in search/parse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve(workspace: str, rel_path: str) -> Path:
    """Resolve a relative path within the workspace, preventing traversal."""
    ws = Path(workspace).resolve()
    target = (ws / rel_path).resolve()
    if not str(target).startswith(str(ws)):
        raise ValueError(f"Path escapes workspace: {rel_path}")
    return target


def _is_excluded(parts: tuple) -> bool:
    """Check if any path component is in the exclude set."""
    return any(p in _EXCLUDED_DIRS for p in parts)


def _run_git(workspace: str, args: List[str], max_output: int = 50_000) -> str:
    """Run a git command inside the workspace."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = proc.stdout
        if len(output) > max_output:
            output = output[:max_output] + "\n... (truncated)"
        return output
    except FileNotFoundError:
        return "(git not found)"
    except subprocess.TimeoutExpired:
        return "(git command timed out)"
    except Exception as exc:
        return f"(git error: {exc})"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def grep(
    workspace: str,
    pattern: str,
    path: Optional[str] = None,
    include_glob: Optional[str] = None,
    max_results: int = 50,
) -> ToolResult:
    """Search for a regex pattern using subprocess grep/rg."""
    search_root = _resolve(workspace, path or ".")
    if not search_root.exists():
        return ToolResult(tool_name="grep", success=False, error=f"Path not found: {path}")

    try:
        re.compile(pattern)
    except re.error as exc:
        return ToolResult(tool_name="grep", success=False, error=f"Invalid regex: {exc}")

    matches: List[Dict] = []
    ws = Path(workspace).resolve()

    if search_root.is_file():
        files_to_search = [search_root]
    else:
        files_to_search = []
        for dirpath, dirnames, filenames in os.walk(search_root):
            rel_dir = Path(dirpath).relative_to(ws)
            if _is_excluded(rel_dir.parts):
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
            for f in filenames:
                fp = Path(dirpath) / f
                if include_glob and not fnmatch.fnmatch(f, include_glob):
                    continue
                if fp.stat().st_size > _MAX_FILE_SIZE:
                    continue
                files_to_search.append(fp)

    compiled = re.compile(pattern)
    for fp in files_to_search:
        if len(matches) >= max_results:
            break
        try:
            text = fp.read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.split("\n"), 1):
            if compiled.search(line):
                matches.append(GrepMatch(
                    file_path=str(fp.relative_to(ws)),
                    line_number=i,
                    content=line.rstrip()[:500],
                ).model_dump())
                if len(matches) >= max_results:
                    break

    return ToolResult(
        tool_name="grep",
        data=matches,
        truncated=len(matches) >= max_results,
    )


def read_file(
    workspace: str,
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> ToolResult:
    """Read file contents with optional line range."""
    fp = _resolve(workspace, path)
    if not fp.is_file():
        return ToolResult(tool_name="read_file", success=False, error=f"File not found: {path}")

    try:
        text = fp.read_text(errors="replace")
    except OSError as exc:
        return ToolResult(tool_name="read_file", success=False, error=str(exc))

    lines = text.split("\n")
    total = len(lines)

    if start_line or end_line:
        s = (start_line or 1) - 1
        e = end_line or total
        selected = lines[s:e]
        content = "\n".join(f"{s + i + 1:>4} | {l}" for i, l in enumerate(selected))
        truncated = e < total
    else:
        if total > 500:
            selected = lines[:500]
            content = "\n".join(f"{i + 1:>4} | {l}" for i, l in enumerate(selected))
            truncated = True
        else:
            content = "\n".join(f"{i + 1:>4} | {l}" for i, l in enumerate(lines))
            truncated = False

    ws = Path(workspace).resolve()
    return ToolResult(
        tool_name="read_file",
        data={"path": str(fp.relative_to(ws)), "total_lines": total, "content": content},
        truncated=truncated,
    )


def list_files(
    workspace: str,
    directory: str = ".",
    max_depth: Optional[int] = 3,
    include_glob: Optional[str] = None,
) -> ToolResult:
    """List files and directories."""
    root = _resolve(workspace, directory)
    if not root.is_dir():
        return ToolResult(tool_name="list_files", success=False, error=f"Directory not found: {directory}")

    ws = Path(workspace).resolve()
    entries: List[Dict] = []
    max_entries = 500

    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(ws)
        depth = len(rel.parts) - len(Path(directory).parts) if directory != "." else len(rel.parts)
        if max_depth and depth >= max_depth:
            dirnames.clear()
            continue
        if _is_excluded(rel.parts):
            dirnames.clear()
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRS)

        for d in dirnames:
            if len(entries) >= max_entries:
                break
            entries.append(FileEntry(path=str(rel / d), is_dir=True).model_dump())

        for f in sorted(filenames):
            if len(entries) >= max_entries:
                break
            if include_glob and not fnmatch.fnmatch(f, include_glob):
                continue
            fp = Path(dirpath) / f
            try:
                size = fp.stat().st_size
            except OSError:
                size = None
            entries.append(FileEntry(
                path=str(rel / f), is_dir=False, size=size,
            ).model_dump())

        if len(entries) >= max_entries:
            break

    return ToolResult(
        tool_name="list_files",
        data=entries,
        truncated=len(entries) >= max_entries,
    )


def find_symbol(
    workspace: str,
    name: str,
    kind: Optional[str] = None,
    _graph_cache: Optional[Dict] = None,
) -> ToolResult:
    """Find symbol definitions using tree-sitter AST parsing."""
    from app.repo_graph.parser import extract_definitions, detect_language

    ws = Path(workspace).resolve()
    results: List[Dict] = []
    name_lower = name.lower()

    for dirpath, dirnames, filenames in os.walk(ws):
        rel_dir = Path(dirpath).relative_to(ws)
        if _is_excluded(rel_dir.parts):
            dirnames.clear()
            continue
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]

        for f in filenames:
            fp = Path(dirpath) / f
            if detect_language(str(fp)) is None:
                continue
            if fp.stat().st_size > _MAX_FILE_SIZE:
                continue

            try:
                source = fp.read_bytes()
            except OSError:
                continue

            rel = str(fp.relative_to(ws))
            symbols = extract_definitions(str(fp), source)

            for defn in symbols.definitions:
                if name_lower not in defn.name.lower():
                    continue
                if kind and defn.kind != kind:
                    continue
                results.append(SymbolLocation(
                    name=defn.name,
                    kind=defn.kind,
                    file_path=rel,
                    start_line=defn.start_line,
                    end_line=defn.end_line,
                    signature=defn.signature,
                ).model_dump())

    return ToolResult(tool_name="find_symbol", data=results)


def find_references(
    workspace: str,
    symbol_name: str,
    file: Optional[str] = None,
) -> ToolResult:
    """Find references to a symbol via grep + AST validation."""
    # First do a grep for the symbol name
    grep_result = grep(
        workspace=workspace,
        pattern=rf"\b{re.escape(symbol_name)}\b",
        path=file,
        max_results=100,
    )
    if not grep_result.success:
        return ToolResult(tool_name="find_references", success=False, error=grep_result.error)

    # Filter grep hits through AST reference data for files that support it
    from app.repo_graph.parser import extract_definitions, detect_language

    ws = Path(workspace).resolve()
    matches = grep_result.data or []
    validated: List[Dict] = []

    # Group by file to avoid re-parsing
    by_file: Dict[str, List[Dict]] = {}
    for m in matches:
        by_file.setdefault(m["file_path"], []).append(m)

    for fpath, file_matches in by_file.items():
        fp = ws / fpath
        if detect_language(str(fp)) is not None and fp.stat().st_size <= _MAX_FILE_SIZE:
            try:
                symbols = extract_definitions(str(fp), fp.read_bytes())
                ref_lines = {r.line for r in symbols.references if r.name == symbol_name}
                for m in file_matches:
                    if m["line_number"] in ref_lines:
                        validated.append(ReferenceLocation(
                            file_path=m["file_path"],
                            line_number=m["line_number"],
                            content=m["content"],
                        ).model_dump())
            except Exception:
                # Fall back to grep matches for this file
                for m in file_matches:
                    validated.append(ReferenceLocation(
                        file_path=m["file_path"],
                        line_number=m["line_number"],
                        content=m["content"],
                    ).model_dump())
        else:
            # Non-parseable files: keep grep matches as-is
            for m in file_matches:
                validated.append(ReferenceLocation(
                    file_path=m["file_path"],
                    line_number=m["line_number"],
                    content=m["content"],
                ).model_dump())

    return ToolResult(tool_name="find_references", data=validated)


def file_outline(workspace: str, path: str) -> ToolResult:
    """Get all definitions in a file."""
    from app.repo_graph.parser import extract_definitions

    fp = _resolve(workspace, path)
    if not fp.is_file():
        return ToolResult(tool_name="file_outline", success=False, error=f"File not found: {path}")

    try:
        source = fp.read_bytes()
    except OSError as exc:
        return ToolResult(tool_name="file_outline", success=False, error=str(exc))

    symbols = extract_definitions(str(fp), source)
    ws = Path(workspace).resolve()
    defs = [
        SymbolLocation(
            name=d.name,
            kind=d.kind,
            file_path=str(fp.relative_to(ws)),
            start_line=d.start_line,
            end_line=d.end_line,
            signature=d.signature,
        ).model_dump()
        for d in symbols.definitions
    ]
    return ToolResult(tool_name="file_outline", data=defs)


def get_dependencies(
    workspace: str,
    file_path: str,
    _graph_service=None,
) -> ToolResult:
    """Find files that this file depends on (out-edges in the dependency graph)."""
    graph = _ensure_graph(workspace, _graph_service)
    if graph is None:
        return ToolResult(
            tool_name="get_dependencies", success=False,
            error="Dependency graph not available (missing networkx or tree-sitter).",
        )

    deps: List[Dict] = []
    for edge in graph.edges:
        if edge.source == file_path:
            deps.append(DependencyInfo(
                file_path=edge.target,
                symbols=edge.symbols,
                weight=edge.weight,
            ).model_dump())

    deps.sort(key=lambda d: d["weight"], reverse=True)
    return ToolResult(tool_name="get_dependencies", data=deps)


def get_dependents(
    workspace: str,
    file_path: str,
    _graph_service=None,
) -> ToolResult:
    """Find files that depend on this file (in-edges in the dependency graph)."""
    graph = _ensure_graph(workspace, _graph_service)
    if graph is None:
        return ToolResult(
            tool_name="get_dependents", success=False,
            error="Dependency graph not available (missing networkx or tree-sitter).",
        )

    deps: List[Dict] = []
    for edge in graph.edges:
        if edge.target == file_path:
            deps.append(DependencyInfo(
                file_path=edge.source,
                symbols=edge.symbols,
                weight=edge.weight,
            ).model_dump())

    deps.sort(key=lambda d: d["weight"], reverse=True)
    return ToolResult(tool_name="get_dependents", data=deps)


def git_log(
    workspace: str,
    file: Optional[str] = None,
    n: int = 10,
) -> ToolResult:
    """Show recent git commits."""
    args = ["log", f"-{n}", "--format=%H|%s|%an|%ai"]
    if file:
        fp = _resolve(workspace, file)
        args += ["--", str(fp)]

    raw = _run_git(workspace, args)
    commits: List[Dict] = []
    for line in raw.strip().split("\n"):
        if not line or line.startswith("("):
            continue
        parts = line.split("|", 3)
        if len(parts) >= 2:
            commits.append(GitCommit(
                hash=parts[0][:8],
                message=parts[1],
                author=parts[2] if len(parts) > 2 else "",
                date=parts[3] if len(parts) > 3 else "",
            ).model_dump())

    return ToolResult(tool_name="git_log", data=commits)


def git_diff(
    workspace: str,
    ref1: Optional[str] = "HEAD~1",
    ref2: Optional[str] = "HEAD",
    file: Optional[str] = None,
) -> ToolResult:
    """Show diff between two git refs."""
    args = ["diff", ref1 or "HEAD~1", ref2 or "HEAD"]
    if file:
        fp = _resolve(workspace, file)
        args += ["--", str(fp)]

    raw = _run_git(workspace, args, max_output=100_000)
    return ToolResult(tool_name="git_diff", data={"diff": raw})


def _find_ast_grep() -> Optional[str]:
    """Locate the ast-grep binary, checking PATH and the venv bin dir."""
    import shutil
    import sys

    found = shutil.which("ast-grep")
    if found:
        return found
    # Check alongside the running Python executable (common in venvs)
    venv_bin = Path(sys.executable).parent / "ast-grep"
    if venv_bin.is_file():
        return str(venv_bin)
    return None


def ast_search(
    workspace: str,
    pattern: str,
    language: Optional[str] = None,
    path: Optional[str] = None,
    max_results: int = 30,
) -> ToolResult:
    """Structural AST search using ast-grep."""
    import json as _json

    ast_grep_bin = _find_ast_grep()
    if ast_grep_bin is None:
        return ToolResult(
            tool_name="ast_search", success=False,
            error="ast-grep not installed. Install with: pip install ast-grep-cli",
        )

    search_root = _resolve(workspace, path or ".")
    if not search_root.exists():
        return ToolResult(tool_name="ast_search", success=False, error=f"Path not found: {path}")

    cmd = [ast_grep_bin, "run", "-p", pattern, "--json"]
    if language:
        cmd += ["-l", language]
    cmd.append(str(search_root))

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=workspace,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(tool_name="ast_search", success=False, error="ast-grep timed out (30s)")

    if proc.returncode not in (0, 1):  # 1 = no matches
        return ToolResult(
            tool_name="ast_search", success=False,
            error=f"ast-grep error: {proc.stderr.strip()[:500]}",
        )

    try:
        raw_matches = _json.loads(proc.stdout) if proc.stdout.strip() else []
    except _json.JSONDecodeError:
        return ToolResult(tool_name="ast_search", success=False, error="Failed to parse ast-grep output")

    ws = Path(workspace).resolve()
    results: List[Dict] = []
    for m in raw_matches[:max_results]:
        file_abs = Path(m.get("file", ""))
        try:
            rel = str(file_abs.relative_to(ws))
        except ValueError:
            rel = str(file_abs)

        # Skip excluded dirs
        if _is_excluded(Path(rel).parts):
            continue

        meta = {}
        single = m.get("metaVariables", {}).get("single", {})
        for var_name, var_data in single.items():
            if isinstance(var_data, dict):
                meta[f"${var_name}"] = var_data.get("text", "")

        rng = m.get("range", {})
        start = rng.get("start", {})
        end = rng.get("end", {})

        text = m.get("text", m.get("lines", ""))
        if len(text) > 1000:
            text = text[:997] + "..."

        results.append(AstMatch(
            file_path=rel,
            start_line=start.get("line", 0) + 1,
            end_line=end.get("line", 0) + 1,
            text=text,
            meta_variables=meta,
        ).model_dump())

    return ToolResult(
        tool_name="ast_search",
        data=results,
        truncated=len(raw_matches) > max_results,
    )


def get_callees(
    workspace: str,
    function_name: str,
    file: str,
) -> ToolResult:
    """Find all functions/methods called within a specific function body."""
    from app.repo_graph.parser import extract_definitions, detect_language

    fp = _resolve(workspace, file)
    if not fp.is_file():
        return ToolResult(tool_name="get_callees", success=False, error=f"File not found: {file}")

    lang = detect_language(str(fp))
    if lang is None:
        return ToolResult(tool_name="get_callees", success=False, error=f"Unsupported language: {file}")

    try:
        source = fp.read_text(errors="replace")
    except OSError as exc:
        return ToolResult(tool_name="get_callees", success=False, error=str(exc))

    # Find the function's line range from AST
    symbols = extract_definitions(str(fp), fp.read_bytes())
    target_def = None
    for d in symbols.definitions:
        if d.name == function_name:
            target_def = d
            break

    if target_def is None:
        return ToolResult(
            tool_name="get_callees", success=False,
            error=f"Function '{function_name}' not found in {file}",
        )

    lines = source.split("\n")

    # When the regex fallback is used, end_line == start_line. In that case
    # infer the end by looking for the next top-level definition or EOF.
    end_line = target_def.end_line
    if end_line <= target_def.start_line:
        next_starts = sorted(
            d.start_line for d in symbols.definitions
            if d.start_line > target_def.start_line
        )
        end_line = (next_starts[0] - 1) if next_starts else len(lines)

    # Extract lines of the function body
    body_lines = lines[target_def.start_line - 1 : end_line]

    # Find function calls in the body using regex
    # Matches: name(...), obj.name(...), but not def name(... or class name(
    call_pattern = re.compile(r'(?<!\bdef\s)(?<!\bclass\s)\b([a-zA-Z_]\w*)\s*\(')
    ws = Path(workspace).resolve()

    seen: set = set()
    callees: List[Dict] = []
    for offset, line in enumerate(body_lines):
        line_no = target_def.start_line + offset
        for match in call_pattern.finditer(line):
            callee_name = match.group(1)
            # Skip Python keywords and builtins that look like calls
            if callee_name in _CALL_NOISE:
                continue
            if callee_name not in seen:
                seen.add(callee_name)
                callees.append(CalleeInfo(
                    callee_name=callee_name,
                    file_path=str(fp.relative_to(ws)),
                    line=line_no,
                ).model_dump())

    return ToolResult(tool_name="get_callees", data=callees)


def get_callers(
    workspace: str,
    function_name: str,
    path: Optional[str] = None,
) -> ToolResult:
    """Find all functions/methods that call a given function."""
    from app.repo_graph.parser import extract_definitions, detect_language

    ws = Path(workspace).resolve()
    search_root = _resolve(workspace, path or ".")
    if not search_root.exists():
        return ToolResult(tool_name="get_callers", success=False, error=f"Path not found: {path}")

    # Regex: function_name followed by ( — a call site
    call_re = re.compile(rf'\b{re.escape(function_name)}\s*\(')

    callers: List[Dict] = []
    for dirpath, dirnames, filenames in os.walk(search_root):
        rel_dir = Path(dirpath).relative_to(ws)
        if _is_excluded(rel_dir.parts):
            dirnames.clear()
            continue
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]

        for f in filenames:
            fp = Path(dirpath) / f
            if detect_language(str(fp)) is None:
                continue
            if fp.stat().st_size > _MAX_FILE_SIZE:
                continue

            try:
                source = fp.read_text(errors="replace")
            except OSError:
                continue

            # Quick check: does the file contain a call?
            if not call_re.search(source):
                continue

            rel = str(fp.relative_to(ws))
            symbols = extract_definitions(str(fp), fp.read_bytes())
            lines = source.split("\n")

            for defn in symbols.definitions:
                if defn.kind not in ("function", "method"):
                    continue
                # Infer end_line when regex fallback sets it == start_line
                end_ln = defn.end_line
                if end_ln <= defn.start_line:
                    next_starts = sorted(
                        d.start_line for d in symbols.definitions
                        if d.start_line > defn.start_line
                    )
                    end_ln = (next_starts[0] - 1) if next_starts else len(lines)
                # Skip the definition line itself (def foo(): matches \bfoo\s*\()
                body_lines = lines[defn.start_line : end_ln]
                for offset, line in enumerate(body_lines):
                    if call_re.search(line):
                        callers.append(CallerInfo(
                            caller_name=defn.name,
                            caller_kind=defn.kind,
                            file_path=rel,
                            line=defn.start_line + 1 + offset,
                            content=line.strip()[:200],
                        ).model_dump())
                        break  # one match per caller is enough

    return ToolResult(tool_name="get_callers", data=callers)


# Noise words to skip when extracting callees
_CALL_NOISE = frozenset({
    "if", "for", "while", "return", "print", "len", "str", "int", "float",
    "bool", "list", "dict", "set", "tuple", "type", "isinstance", "issubclass",
    "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "super", "property", "staticmethod", "classmethod", "getattr", "setattr",
    "hasattr", "delattr", "open", "repr", "hash", "id", "input", "abs",
    "min", "max", "sum", "round", "any", "all", "next", "iter",
})


# ---------------------------------------------------------------------------
# Graph helper
# ---------------------------------------------------------------------------

_GRAPH_TTL_SECONDS = 120  # rebuild graph after 2 minutes

_graph_cache: Dict[str, tuple] = {}  # workspace → (graph, monotonic_time)


def _ensure_graph(workspace: str, graph_service=None):
    """Build or return a cached dependency graph for the workspace."""
    import time

    entry = _graph_cache.get(workspace)
    if entry is not None:
        graph, ts = entry
        if (time.monotonic() - ts) < _GRAPH_TTL_SECONDS:
            return graph
        # expired — fall through to rebuild

    graph = None
    if graph_service is not None:
        graph = graph_service.build_graph(workspace)
    else:
        try:
            from app.repo_graph.graph import build_dependency_graph
            graph = build_dependency_graph(workspace)
        except ImportError:
            logger.warning("repo_graph not available — graph tools disabled.")
            return None

    _graph_cache[workspace] = (graph, time.monotonic())
    return graph


def invalidate_graph_cache(workspace: Optional[str] = None) -> None:
    """Clear graph cache (call after file changes)."""
    if workspace:
        _graph_cache.pop(workspace, None)
    else:
        _graph_cache.clear()


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

TOOL_REGISTRY = {
    "grep": grep,
    "read_file": read_file,
    "list_files": list_files,
    "find_symbol": find_symbol,
    "find_references": find_references,
    "file_outline": file_outline,
    "get_dependencies": get_dependencies,
    "get_dependents": get_dependents,
    "git_log": git_log,
    "git_diff": git_diff,
    "ast_search": ast_search,
    "get_callees": get_callees,
    "get_callers": get_callers,
}


def execute_tool(tool_name: str, workspace: str, params: Dict[str, Any]) -> ToolResult:
    """Execute a tool by name with the given parameters."""
    fn = TOOL_REGISTRY.get(tool_name)
    if fn is None:
        return ToolResult(tool_name=tool_name, success=False, error=f"Unknown tool: {tool_name}")

    try:
        return fn(workspace=workspace, **params)
    except Exception as exc:
        logger.exception("Tool %s failed", tool_name)
        return ToolResult(tool_name=tool_name, success=False, error=str(exc))
