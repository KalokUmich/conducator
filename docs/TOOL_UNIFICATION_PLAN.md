# Tool Unification Plan

> **Status: Option E — IMPLEMENTED** (see [Implementation Notes](#implementation-notes) below)

## Problem

Conductor has 24 code tools implemented twice:
- **Python** (`backend/app/code_tools/tools.py`, 3648 lines) — used by backend in git-worktree mode
- **TypeScript** (`extension/src/extension.ts` `_executeLocalTool()`, 800 lines) — used by extension in local mode

No shared definitions. Parameter names diverge (`file` vs `file_path` vs `path`), result formats diverge (`file_path` vs `file`). Multiple bugs caused by forgetting to sync changes.

## Options

### Option A: Python CLI — Extension calls Python via subprocess

Extension runs `python -m app.code_tools <tool> <workspace> '<params>'` for every tool call.

```
Extension receives tool_request
  → localToolRunner.ts
  → python -m app.code_tools grep /workspace '{"pattern":"foo"}'
  → JSON stdout → ToolResult
  → (optional) lspEnhancer.ts for 6 AST tools
```

| Pros | Cons |
|------|------|
| Zero code duplication | Requires Python 3.10+ on dev machine |
| Single test suite (98 existing tests) | Requires pip install tree-sitter, ast-grep, etc |
| Param validation + repair included | ~100ms subprocess startup per tool call |
| Output policies included | Frontend devs may not have Python |
| LSP enhancement still possible | Extra dependency management |

**Migration effort**: Medium. Create CLI entry point + TS wrapper. Existing TS code moves to fallback.

---

### Option B: Declarative YAML + Code Generation

Define tools in YAML, generate both Python and TypeScript implementations.

```yaml
tools:
  - name: grep
    type: subprocess
    command: grep
    args: ["-rn", "--include", "{include_glob}", "-m", "{max_results}", "{pattern}", "{path}"]
    result_parser: grep_lines
```

| Pros | Cons |
|------|------|
| Single source of truth | Only works for simple subprocess tools |
| Auto-generates both sides | Can't express complex logic (trace_variable, detect_patterns) |
| Type safety via generation | Generated code hard to debug |
| | Still need manual impl for ~10 complex tools |

**Migration effort**: High. Need code gen framework + manual overrides for complex tools.

---

### Option C: Build-time Python → TypeScript transpilation

At `npm run compile` time, parse Python tool source code and generate TypeScript equivalents.

```
build step:
  tools.py → AST parse → extract subprocess calls → generate TS child_process calls
  tools.py → AST parse → extract file I/O → generate TS fs calls
```

| Pros | Cons |
|------|------|
| True single source (Python) | Very complex build tooling |
| No runtime Python dependency | Can't transpile tree-sitter/networkx calls |
| Catches divergence at compile time | Custom transpiler = maintenance burden |
| | Only works for simple patterns |

**Migration effort**: Very high. Building a reliable Python→TS transpiler is a project in itself.

---

### Option D: web-tree-sitter — Native AST in TypeScript

Use [web-tree-sitter](https://github.com/nicholasgasior/web-tree-sitter) (official WASM build) to give the extension the same AST parsing capability as the Python backend.

```
Extension:
  subprocess tools → child_process (same as now)
  AST tools → web-tree-sitter WASM (replaces both tree-sitter-python AND VS Code LSP fallback)
  complex tools → rewrite in TS using web-tree-sitter
```

| Pros | Cons |
|------|------|
| No Python dependency | Need to rewrite all AST tools in TS |
| Same precision as Python tree-sitter | Still two implementations (Python + TS) |
| Works offline, no LSP needed | web-tree-sitter grammar loading is non-trivial |
| WASM is fast (~native speed) | Larger extension bundle (~5MB for grammars) |
| Language grammars: Python, JS/TS, Java, Go, Rust, C, C++ all available | |

**Migration effort**: High. Port all tree-sitter usage from Python to TS. But result is a proper single-language solution.

---

### Option E: Hybrid — Python CLI + web-tree-sitter ✅ IMPLEMENTED

Combine Option A and D:
- **Subprocess tools** (11 tools): Implement once in TS (they're trivial child_process calls)
- **AST tools** (6 tools): Use web-tree-sitter in TS (same quality as Python)
- **Complex analysis tools** (7 tools): Python CLI subprocess (these have the most logic)

```
Extension:
  grep, git_*, list_files, read_file → TS child_process (native)
  find_symbol, file_outline, etc → web-tree-sitter WASM (native)
  trace_variable, detect_patterns, etc → python -m app.code_tools (CLI)

Fallback: All 24 tools can run via Python CLI if web-tree-sitter fails
```

| Pros | Cons |
|------|------|
| Best of both worlds | Three different execution strategies |
| No Python needed for 17/24 tools | Still need Python for 7 complex tools |
| web-tree-sitter = same quality as backend | More complex architecture |
| LSP can still enhance on top | |

**Migration effort**: Medium-high. Need web-tree-sitter setup + Python CLI for complex tools.

---

### Option F: Shared tool schema + strict parity testing (pragmatic)

Keep both implementations, but enforce consistency:

1. **Shared schema** (`config/tools/tool_definitions.json`) generated from Python Pydantic models
2. **TS code reads this schema** for param names, types, defaults — no more ad-hoc `params.path || params.file_path`
3. **Automated parity test** in CI: run each tool through both Python and TS with same inputs, assert same output structure
4. **Pre-commit hook**: if `tools.py` or `schemas.py` changes, regenerate schema and fail if TS doesn't match

```
Python schemas.py → generate → config/tools/tool_definitions.json
                                    ↓
                              TS reads at runtime
                              (param names, types, defaults)
                                    ↓
                              _executeLocalTool uses schema
                              (no more hardcoded param aliases)
```

| Pros | Cons |
|------|------|
| Minimal migration effort | Still two implementations |
| Catches divergence in CI | Won't catch logic bugs |
| No new dependencies | TS tools may still drift in behavior |
| Works today | Not a true unification |

**Migration effort**: Low. Schema gen script + TS schema loader + CI check.

---

## Comparison Matrix

| Criteria | A (CLI) | B (YAML) | C (Transpile) | D (WASM) | E (Hybrid) ✅ | F (Schema) |
|----------|---------|----------|---------------|----------|------------|------------|
| Code duplication | None | Partial | None | Full TS rewrite | Low | Full (enforced) |
| Python dependency | Required | None | None | None | Partial (7 tools) | None |
| Migration effort | Medium | High | Very High | High | Medium-High | Low |
| AST quality | tree-sitter | N/A | N/A | tree-sitter WASM | tree-sitter WASM | LSP + grep |
| Runtime overhead | ~100ms/call | None | None | None | Mixed | None |
| Maintenance burden | Low (single impl) | Medium (codegen) | High (transpiler) | Medium (two langs) | Medium | Medium (parity tests) |
| New tool workflow | Add Python only | Add YAML + verify | Add Python only | Add Python + TS | Add Python + maybe TS | Add both + test |

---

## Implementation Notes

**Decision: Option E — Implemented.**

### Files Created

**Extension (TypeScript):**

| File | Purpose |
|------|---------|
| `extension/src/services/treeSitterService.ts` | web-tree-sitter WASM wrapper. Port of `backend/app/repo_graph/parser.py`. Provides `extractDefinitions()`, `detectLanguage()`, lazy parser caching per language. Supports 8 languages (Python, JS, TS, Java, Go, Rust, C, C++). |
| `extension/src/services/astToolRunner.ts` | 6 AST tools using treeSitterService: `file_outline`, `find_symbol`, `find_references`, `get_callees`, `get_callers`, `expand_symbol`. Key fix: `get_callees` scopes regex to function body only (prevents false positives). |
| `extension/src/services/pythonCliRunner.ts` | Spawns `python -m app.code_tools` subprocess with 30s timeout. Auto-discovers Python (`findPython()`) and backend path (`findBackendPath()`). `isAvailable()` checks before use. |
| `extension/src/services/localToolDispatcher.ts` | Three-tier routing: SUBPROCESS_TOOLS (11) → AST_TOOLS (6) → COMPLEX_TOOLS (7). Full fallback chain: tier fails → try Python CLI → try original TS handler. |

**Backend (Python):**

| File | Purpose |
|------|---------|
| `backend/app/code_tools/__main__.py` | CLI entry point: `python -m app.code_tools <tool> <workspace> '<json_params>'`. Supports `list` command. JSON stdout. Suppressed logging (clean output for TS parsing). |

**Extension (Grammar WASM files):**

| Location | Contents |
|----------|---------|
| `extension/grammars/` | 9 `.wasm` files committed as project files: `tree-sitter.wasm` runtime + 8 language grammars (Python, JS, TS, Java, Go, Rust, C, C++). ~8MB total. |
| `extension/scripts/download-grammars.sh` | Downloads grammars from GitHub releases. `--latest` flag for CI/Docker. Pinned versions for reproducibility. |

### Tool Tier Assignment

**Subprocess tools (11) — pure `child_process` in TS:**
`grep`, `read_file`, `list_files`, `git_log`, `git_diff`, `git_blame`, `git_show`, `get_dependencies`, `get_dependents`, `find_tests`, `run_test`

**AST tools (6) — web-tree-sitter in TS:**
`file_outline`, `find_symbol`, `find_references`, `get_callees`, `get_callers`, `expand_symbol`

**Complex tools (6) — native TypeScript (complexToolRunner.ts):**
`compressed_view`, `trace_variable`, `detect_patterns`, `get_dependencies`, `get_dependents`, `test_outline`

> **Update:** All tools now run in native TypeScript. The Python CLI approach was removed because the extension is distributed as a .vsix — users won't have the backend source code. `module_summary` and `ast_search` moved to Tier 1 (subprocess).

### Makefile Commands

```bash
make update-grammars         # Re-download pinned grammar versions
make update-grammars LATEST=1  # Always fetch latest from GitHub releases
```

The Docker build (`Dockerfile`) always fetches latest grammars during image build so the container stays up to date.

### Architecture Diagram

```
extension receives tool_request (from backend RemoteToolExecutor → WebSocket)
  ↓
localToolDispatcher.ts (ALL native TypeScript — zero Python dependency)
  ├── SUBPROCESS (12) → child_process (rg, git) → ToolResult
  ├── AST (6) → treeSitterService.ts → astToolRunner.ts → ToolResult
  └── COMPLEX (6) → complexToolRunner.ts → ToolResult

  [fallback at each tier: if tier fails, try legacy subprocess implementation]
```

### Parity Testing

Every tool must produce equivalent output in both Python (backend) and TypeScript (extension):

- `backend/tests/test_local_tools_parity.py` — runs Python tools against conductor project
- `eval/tool_comparison.py` — generates baselines for Python vs TS comparison
- New tools must have parity tests before merge

### Known Limitations

- `explore_synthesizer` WARNING: agent declares inputs `{raw_evidence, perspective_answers}` not always available in business_flow_tracing pipeline. Cosmetic — does not break functionality.
