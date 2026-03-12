"""System prompts for the agent loop — 3-layer architecture.

Layer 1: CORE_IDENTITY (~100 lines) — always included
Layer 2: STRATEGY (~30 lines) — selected by query classifier
Layer 3: Runtime Guidance — injected dynamically by service.py (budget, scatter, etc.)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Directories to skip during layout scanning (mirrors tools._EXCLUDED_DIRS)
_EXCLUDED_DIRS: Set[str] = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "target",
    "dist", "vendor", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".tox", "build", ".next", ".nuxt",
}

# Files that identify a project root / source root
_PROJECT_MARKERS: Set[str] = {
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "setup.py", "setup.cfg", "pyproject.toml", "requirements.txt",
    "package.json", "tsconfig.json",
    "go.mod", "Cargo.toml",
    "*.csproj", "*.sln",
    "Makefile", "CMakeLists.txt", "Dockerfile",
}

_KEY_DOC_FILES: List[str] = [
    "README.md", "README.rst", "README.txt", "README",
    "CLAUDE.md", "ARCHITECTURE.md", "DESIGN.md", "OVERVIEW.md",
    "CONTRIBUTING.md", "docs/README.md", "docs/architecture.md",
]

_DOC_TRUNCATE_CHARS = 8000


# ═══════════════════════════════════════════════════════════════════════
# LAYER 1: Core Identity (always included, ~4000 tokens)
# ═══════════════════════════════════════════════════════════════════════

CORE_IDENTITY = """\
You are a code intelligence agent. You navigate large codebases to answer \
questions with precision and evidence.

## Workspace
Operating inside: {workspace_path}

{workspace_layout_section}

{project_docs_section}

## Budget
You have {max_iterations} tool-calling iterations. Reserve the last 1-2 for verification.

## Core Behavior

1. **HYPOTHESIS-DRIVEN**: Before each tool call, state what you expect to find and why.
2. **EVIDENCE-BASED**: Every claim must reference a specific file and line number.
3. **SCOPE SEARCHES**: Use the `path` parameter in grep/find_symbol to target the \
relevant project root from "Detected project roots" above. Never search the entire \
workspace when a specific project directory is known.
4. **READ ACTUAL CODE**: compressed_view shows structure but not logic. When tracing \
a flow, debugging, or understanding behavior, use read_file or expand_symbol to see \
the real implementation. In Java, always read the *Impl class, not just the interface.
5. **BUDGET-AWARE**: Monitor [Budget: ...] tags. Converge when budget runs low.

## Hard Constraints

- **Never re-read a file you already read.** Use start_line/end_line for specific sections.
- **Never read a large file (>200 lines) without file_outline first.**
- **Never use more than 2 broad greps in a row.** After locating, switch to reading.
- **Do NOT pass include_glob to grep** unless you are certain about the file extension. \
The workspace may contain multiple languages.

## Tool Guide (when to use what)

| Tool | Best for | Token cost |
|------|----------|------------|
| grep / find_symbol | Locating specific names, patterns, entry points | Low |
| read_file / expand_symbol | Understanding actual logic, control flow, conditionals | Medium |
| file_outline | Seeing all definitions in a file before reading sections | Low |
| get_callees / get_callers | Following call chains between functions | Low |
| compressed_view | Getting a file's structure without reading it fully | Low |
| module_summary | Understanding a directory's purpose and contents | Low |
| find_tests | Finding test files that document expected behavior | Low |
| trace_variable | Tracking data flow across function boundaries | Medium |

**Choose tools based on the strategy below, not this table's order.**

## Answer Format

- **Direct answer** (1-3 sentences)
- **Evidence**: file paths, line numbers, relevant code
- **Call chain or data flow** (if applicable): Entry → A → B → C
- **Caveats**: uncertainties, areas not fully traced
"""


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2: Strategies (selected by query classifier, ~30 lines each)
# ═══════════════════════════════════════════════════════════════════════

STRATEGIES = {
    "entry_point_discovery": """\
## Strategy: Entry Point Discovery
1. grep for route/endpoint patterns matching the query terms
2. Use find_symbol to locate handler functions
3. Use compressed_view on the handler file to understand structure
4. Trace inward using get_callees if the handler delegates
Target: 3-6 iterations. Answer with the entry point file, function, and line number.""",

    "business_flow_tracing": """\
## Strategy: Business Flow Tracing
1. **Find entry point**: grep or find_symbol for the domain term — scope to the \
relevant project root (check "Detected project roots" for pom.xml, package.json, etc.)
2. **Read the implementation**: Use read_file or expand_symbol on the handler/service. \
For Java, always find and read the *Impl class, not just the interface.
3. **Follow the call chain**: Use get_callees on each method, then read_file/expand_symbol \
on the next service in the chain. Build the flow step by step.
4. **Check tests for flow documentation**: Use find_tests or grep in test directories — \
E2E/integration tests often show the complete journey in order.
5. **Trace data transformations**: If the flow involves state changes, use trace_variable.
6. Summarize: Entry → Step 1 → Step 2 → ... → Final state, each citing file:line.
Target: 8-15 iterations. Read actual code, not just summaries.""",

    "root_cause_analysis": """\
## Strategy: Root Cause Analysis
1. Find the error location (grep for error messages, exception types)
2. Use expand_symbol to read the error context in detail
3. Trace callers using get_callers — how do we reach this error?
4. Check data flow using trace_variable — what input causes the failure?
5. Check recent changes using git_log/git_diff for regression clues
Target: 8-15 iterations. Answer with root cause, evidence chain, and fix suggestion.""",

    "impact_analysis": """\
## Strategy: Impact Analysis
1. Find all dependents using get_dependents (who depends on this code?)
2. Use find_references to find all call sites
3. Use find_tests to identify test coverage
4. For each affected module, use compressed_view to assess severity
5. Summarize: affected modules, affected APIs, risk level
Target: 6-12 iterations. Answer with impact summary and risk assessment.""",

    "architecture_question": """\
## Strategy: Architecture Overview
1. Use module_summary on top-level directories to understand responsibilities
2. Use get_dependencies to map module relationships
3. Use compressed_view on key service files for interface details
4. Build a dependency diagram: Module → depends on → Module
Target: 5-10 iterations. Answer with architecture summary and module diagram.
IMPORTANT: Start from documentation and module_summary — do NOT read individual files.""",

    "config_analysis": """\
## Strategy: Config Analysis
1. grep for the config key/setting name
2. Use find_references to find all consumers
3. Use trace_variable to understand how the config value flows
4. Use compressed_view on consumer files for context
Target: 3-6 iterations. Answer with where the config is defined, who uses it, and how.""",

    "data_lineage": """\
## Strategy: Data Lineage Tracing
1. Find the data source (grep the variable/field name, or find_symbol for the model)
2. Use trace_variable forward to find where the value flows
3. Chain trace_variable calls: each hop's flows_to becomes the next starting point
4. Use read_file to verify ambiguous hops (confidence="low")
5. Map the complete lineage: Source → Transform → Sink
Target: 8-15 iterations. Answer with complete data flow chain, citing file:line at each hop.""",

    "recent_changes": """\
## Strategy: Recent Changes / Git History
1. **Start with git_log** to see recent commits (optionally filtered to a file or path).
2. **Use git_show** on interesting commits to read the full commit message and diff.
3. **Use git_diff** to compare specific refs (e.g. HEAD~5..HEAD) or branches.
4. **Use git_blame** on specific files/lines to trace authorship.
5. **Read affected code** with read_file to understand the context of changes.
Target: 3-8 iterations. Answer with commit hashes, authors, dates, and what changed.""",
}

# Default strategy for unknown query types
_DEFAULT_STRATEGY = STRATEGIES["business_flow_tracing"]


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _read_key_docs(workspace_path: str) -> str:
    """Read key documentation files from the workspace root(s)."""
    ws = Path(workspace_path).resolve()
    if not ws.is_dir():
        return ""

    found: List[str] = []
    seen_names: set = set()

    search_dirs = [ws]
    try:
        search_dirs.extend(
            p for p in sorted(ws.iterdir())
            if p.is_dir() and p.name not in _EXCLUDED_DIRS
        )
    except OSError:
        pass

    for search_dir in search_dirs:
        for doc_name in _KEY_DOC_FILES:
            if doc_name.lower() in seen_names:
                continue
            doc_path = search_dir / doc_name
            if not doc_path.is_file():
                continue
            try:
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                rel = doc_path.relative_to(ws)
                if len(content) > _DOC_TRUNCATE_CHARS:
                    content = content[:_DOC_TRUNCATE_CHARS] + "\n... (truncated)"
                found.append(f"#### {rel}\n```\n{content}\n```")
                seen_names.add(doc_name.lower())
            except OSError:
                continue

    return "\n\n".join(found) if found else ""


def scan_workspace_layout(
    workspace_path: str,
    max_depth: int = 3,
    max_entries: int = 120,
) -> str:
    """Scan the workspace and return a compact tree + detected project roots.

    Two-phase scan:
      1. Walk ALL directories (up to max_depth) for project markers — never
         truncated, so pom.xml deep in ``loan/`` is always detected.
      2. Build the directory tree with a **per-directory file cap** so that
         one large directory (e.g. CDE/) cannot consume the entire budget.
    """
    ws = Path(workspace_path).resolve()
    if not ws.is_dir():
        return ""

    # ------------------------------------------------------------------
    # Phase 1: Detect project markers across the FULL tree (no entry cap)
    # ------------------------------------------------------------------
    project_roots: List[str] = []
    for dirpath, dirnames, filenames in os.walk(ws):
        rel = Path(dirpath).relative_to(ws)
        depth = len(rel.parts)
        if depth >= max_depth:
            dirnames.clear()
            continue
        if any(p in _EXCLUDED_DIRS for p in rel.parts):
            dirnames.clear()
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRS)
        markers_here = sorted(set(filenames) & _PROJECT_MARKERS)
        if markers_here:
            rel_str = str(rel) if str(rel) != "." else "(root)"
            project_roots.append(f"  {rel_str}/ — {', '.join(markers_here)}")

    # ------------------------------------------------------------------
    # Phase 2: Build the tree with fair budget allocation
    # ------------------------------------------------------------------
    # Count top-level directories so we can cap files per directory.
    try:
        top_items = sorted(ws.iterdir())
        top_dirs = [
            p.name for p in top_items
            if p.is_dir() and p.name not in _EXCLUDED_DIRS
        ]
    except OSError:
        top_dirs = []
    # Each top-level dir gets at most this many file entries at depth 1.
    files_per_top_dir = max(8, max_entries // max(len(top_dirs) + 1, 1))

    tree_lines: List[str] = []
    # Track how many file entries each top-level dir has used.
    top_dir_file_count: Dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(ws):
        rel = Path(dirpath).relative_to(ws)
        depth = len(rel.parts)

        if depth >= max_depth:
            dirnames.clear()
            continue
        if any(p in _EXCLUDED_DIRS for p in rel.parts):
            dirnames.clear()
            continue

        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRS)

        indent = "  " * depth
        if depth > 0:
            tree_lines.append(f"{indent}{rel.name}/")

        # List files at depth 0 (root) and depth 1 (inside top-level dirs),
        # capped per top-level directory.
        if depth <= 1:
            top_dir = rel.parts[0] if depth == 1 else "(root)"
            used = top_dir_file_count.get(top_dir, 0)
            for f in sorted(filenames):
                if used >= files_per_top_dir:
                    remaining = len(filenames) - used
                    if remaining > 0:
                        tree_lines.append(f"{indent}  ... ({remaining} more files)")
                    break
                tree_lines.append(f"{indent}  {f}")
                used += 1
            top_dir_file_count[top_dir] = used

        # Show subdirectory names at the boundary depth
        for d in dirnames:
            if depth + 1 >= max_depth:
                tree_lines.append(f"{indent}  {d}/")

        if len(tree_lines) >= max_entries:
            tree_lines.append(f"{indent}  ... (truncated)")
            break

    result_parts: List[str] = []

    if tree_lines:
        result_parts.append("### Directory layout (depth ≤ 3)\n```\n" + "\n".join(tree_lines) + "\n```")

    if project_roots:
        result_parts.append(
            "### Detected project roots\n"
            + "\n".join(project_roots)
            + "\n\n"
            + "**Source code is likely under these directories.** "
            + "Always use the correct subdirectory when calling tools."
        )

    return "\n\n".join(result_parts)


def build_system_prompt(
    workspace_path: str,
    workspace_layout: Optional[str] = None,
    project_docs: Optional[str] = None,
    max_iterations: int = 20,
    query_type: Optional[str] = None,
) -> str:
    """Build the full system prompt from 3 layers.

    Parameters
    ----------
    workspace_path:
        Absolute path to the workspace root.
    workspace_layout:
        Pre-computed workspace layout string.
    project_docs:
        Pre-computed project documentation string.
    max_iterations:
        Maximum number of tool-calling iterations.
    query_type:
        Query type from classifier. Selects the Layer 2 strategy.
    """
    if workspace_layout is None:
        workspace_layout = scan_workspace_layout(workspace_path)
    if project_docs is None:
        project_docs = _read_key_docs(workspace_path)

    docs_section = ""
    if project_docs:
        docs_section = (
            "### Project documentation (auto-detected)\n"
            "Use this to understand the project before diving into code.\n\n"
            + project_docs
        )

    # Layer 1: Core Identity
    prompt = CORE_IDENTITY.format(
        workspace_path=workspace_path,
        workspace_layout_section=workspace_layout,
        project_docs_section=docs_section,
        max_iterations=max_iterations,
    )

    # Layer 2: Strategy (selected by query classifier)
    strategy = STRATEGIES.get(query_type or "", _DEFAULT_STRATEGY)
    prompt += "\n\n" + strategy

    return prompt
