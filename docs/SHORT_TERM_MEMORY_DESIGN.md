# Short-Term Memory — Fact Vault Design

**Status**: Design draft for Phase 9.15
**Author**: 2026-04-18 session
**Target sprint**: 16

## Why this exists

The PR Brain dispatches up to 7 parallel review sub-agents per PR. Each runs
its own exploration via `grep`, `read_file`, `find_symbol`, `get_dependencies`.
In practice these queries overlap heavily — multiple agents grep the same
patterns, re-read the same hot files, and each separately triggers the
`_ensure_graph` dependency-graph build over the full workspace.

A single pathological case (sentry-006 in Greptile benchmark) spent ~50
minutes partly because 7 agents concurrently stampeded the graph build on a
17K-file repo, wasting ~7× the CPU and budget that a single build would
have used. Beyond this specific bug, sub-agents routinely re-run identical
tool calls that other agents answered seconds earlier.

A **task-scoped fact vault** — a SQLite-backed store created per PR review
and destroyed at the end — lets sub-agents share tool-call results without
paying for them repeatedly. It also gives us a place to record "verified
NOT found" negative facts so Haiku stops hallucinating the same missing
symbol across agents.

## Non-goals

- **Long-term / cross-session memory.** Persistent learnings across PRs are a
  separate initiative (future extension of 9.15 + 9.17 consolidation hook).
- **Semantic / vector retrieval.** Exact-key lookup and range-intersection
  cover >90% of real sub-agent queries. Vectors add latency and failure
  modes with no measurable accuracy gain at our scale.
- **Dedicated "librarian" sub-agent.** Per Claude Code's design: persistent
  agents are only worth it when they need identity across turns. Memory
  work is stateless; a service function library is cheaper. If we ever
  need LLM reasoning over facts (relevance judgment), follow Claude Code's
  `sideQuery` pattern — an inline call, not a standing agent.

## Architecture summary

```
┌─────────────────────────────────────────────────────────────┐
│ PR Brain Orchestrator                                       │
│                                                             │
│   on_task_start:                                            │
│     session_id = uuid4()                                    │
│     vault = FactStore.create(session_id)  # SQLite + WAL    │
│     inject vault path into sub-agent prompts (optional)     │
│                                                             │
│   dispatch sub-agents in parallel                           │
│          ↓                                                  │
│   each sub-agent uses CachedToolExecutor wrapping           │
│   execute_tool — check vault → miss → run → put → return    │
│                                                             │
│   on_synthesize_complete (9.17 hook):                       │
│     vault.close()                                           │
│     os.unlink(sqlite_path)                                  │
└─────────────────────────────────────────────────────────────┘
```

## Storage — SQLite with WAL mode

One SQLite file per PR review session at
`~/.conductor/scratchpad/{session_id}.sqlite`. WAL mode lets concurrent
writers (worker threads) insert without blocking readers.

### Schema

```sql
CREATE TABLE facts (
    key         TEXT PRIMARY KEY,        -- canonical key, see below
    tool        TEXT NOT NULL,           -- 'grep', 'read_file', ...
    path        TEXT,                    -- redundant but enables fast filter
    range_start INTEGER,                 -- populated for read_file/git_blame
    range_end   INTEGER,                 --
    content     BLOB NOT NULL,           -- zlib-compressed JSON result
    agent       TEXT,                    -- which sub-agent produced it
    ts_written  INTEGER NOT NULL         -- epoch ms
);

CREATE INDEX idx_tool_path
    ON facts(tool, path, range_start, range_end);

CREATE TABLE negative_facts (
    key         TEXT PRIMARY KEY,        -- canonical key of the failed query
    tool        TEXT NOT NULL,
    query       TEXT NOT NULL,           -- human-readable description
    reason      TEXT,                    -- why it's a negative (not found / failed)
    ts_written  INTEGER NOT NULL
);

CREATE TABLE meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL                      -- session metadata (started, workspace, …)
);
```

### Canonical keys (schema version prefix)

Every cache key starts with `v1:` so a future tool-semantics change can
invalidate old entries by bumping to `v2:`.

| Tool            | Key format                                                    | Notes |
|-----------------|---------------------------------------------------------------|-------|
| `grep`          | `v1:grep:{pattern}:{path}:{glob}:{type}`                      | pattern trimmed, globs sorted |
| `read_file`     | `v1:read_file:{abs_path}:{start}:{end}`                       | range-intersection on lookup |
| `find_symbol`   | `v1:find_symbol:{symbol}:{path_prefix}`                       | exact symbol match |
| `ast_search`    | `v1:ast_search:{pattern_hash16}:{path}`                       | SHA256 of canonical pattern |
| `get_dependencies` | `v1:get_dependencies:{abs_path}`                           | per-file |
| `ensure_graph`  | `v1:ensure_graph:{abs_workspace}`                             | workspace singleton |
| `file_outline`  | `v1:file_outline:{abs_path}`                                  | full file |
| `git_diff`      | `v1:git_diff:{spec}:{abs_path}`                               | spec normalised |

**Canonicalisation rules** (must apply before hashing/storing):
- paths: absolute, symlinks resolved
- patterns: strip leading/trailing whitespace, normalise escapes
- globs / type sets: sorted alphabetically before join
- lowercase tool names

**Range-intersection for read_file** (the `subagent B 想要 100-101` case):

```sql
-- request: read_file(path='/abs/sentry/paginator.py', start=101, end=130)
SELECT content FROM facts
WHERE tool = 'read_file'
  AND path = '/abs/sentry/paginator.py'
  AND range_start <= 101
  AND range_end   >= 130
ORDER BY (range_end - range_start) ASC    -- narrowest superset wins
LIMIT 1;
```

The follower slices `100-150`'s cached content to the requested `101-130`
range. No re-disk-read.

## Concurrency — in-flight dedup

The immediate production bug is 7 concurrent threads calling
`_ensure_graph` on a cold cache. Solution: **per-key double-checked
locking**, not a global lock.

```python
# backend/app/scratchpad/inflight.py

import threading
from typing import Dict

_build_locks: Dict[str, threading.Lock] = {}
_meta_lock = threading.Lock()

def key_lock(key: str) -> threading.Lock:
    """Get-or-create a lock for a specific cache key.

    Different keys don't serialise against each other.
    """
    with _meta_lock:
        return _build_locks.setdefault(key, threading.Lock())
```

Caller pattern:

```python
def _ensure_graph(workspace: str):
    # Fast path — no lock if cache is fresh
    cached = _get_cached(workspace)
    if cached:
        return cached

    # Slow path — serialise cold-miss builders for this workspace
    with key_lock(f"v1:ensure_graph:{workspace}"):
        cached = _get_cached(workspace)           # re-check under lock
        if cached:
            return cached
        graph = build_dependency_graph(workspace)
        _put_cache(workspace, graph)
        return graph
```

**Properties**:
- Different workspaces can build in parallel (per-key lock).
- Followers of a cold-miss block until the leader finishes, then find
  the fresh value via the double-check.
- No Future/Promise plumbing needed — threading.Lock is enough when the
  result is stored in the cache by the leader.

**When to reach for `threading.Event` / `Future` instead**: if the result
isn't persisted in a shared location (pure in-memory pipeline, no cache
layer). We have a cache, so Lock is cleaner.

## CachedToolExecutor — transparent cache wrapping

Sub-agents don't know the vault exists. Wrap `execute_tool`:

```python
class CachedToolExecutor:
    def __init__(self, store: FactStore, inner: ToolExecutor):
        self._store = store
        self._inner = inner

    def execute(self, tool_name, workspace, params, agent):
        key = canonical_key(tool_name, params)
        if not is_cacheable(tool_name):
            return self._inner.execute(tool_name, workspace, params, agent)

        # Range-intersecting tools use a custom lookup
        if tool_name in RANGE_TOOLS:
            hit = self._store.range_lookup(tool_name, params)
        else:
            hit = self._store.get(key)

        if hit is not None:
            return hit

        neg = self._store.get_negative(key)
        if neg is not None:
            return ToolResult(success=False, error=f"cached negative: {neg.reason}")

        result = self._inner.execute(tool_name, workspace, params, agent)
        if result.success:
            self._store.put(key, tool_name, params, result, agent)
        elif is_negative_cacheable(result):
            self._store.put_negative(key, tool_name, params, result.error)
        return result
```

**`is_cacheable` exclusions**: `run_test` (results can change), `file_edit`
/ `file_write` (side-effects), `web_search` / `web_navigate` (non-
determinism). Reads and queries are cacheable.

**Negative cache criteria**: only cache a negative if the failure is of
shape "X does not exist / was not found". Do NOT cache transient failures
(timeout, ExpiredToken, BedrockThrottle).

## INDEX.md — paper-style dump

A CLI `python -m app.scratchpad dump <session_id>` renders the SQLite
into a human-inspectable markdown index:

```markdown
# Scratchpad Index — session abc123
Created: 2026-04-18T18:20:00  Workspace: /home/kalok/abound-server

## Summary
- grep: 23 queries (12 unique patterns)
- read_file: 47 ranges across 31 files
- find_symbol: 8 symbols (3 negative)
- ensure_graph: 1 (17K files, took 8 min, cached 42min ago)

## Recent facts (last 20)
| Time | Tool | Key | Scope | By agent |
|---|---|---|---|---|
| 18:25:14 | read_file | sentry/paginator.py:821-913 | 93 lines | correctness |
| 18:24:58 | grep | `OptimizedCursorPaginator` | repo-wide, 12 hits | correctness |
| 18:24:30 | find_symbol | `paginate` (negative: not found) | BasePaginator | correctness_b |

## By tool

### read_file (sorted by file, then range)
- sentry/paginator.py:
  - 1–50 (security)
  - 821–913 (correctness)
- sentry/api/endpoints/organization_auditlogs.py:
  - 1–100 (correctness)

### grep (inverted by pattern prefix)
- `Optimized*`: 3 queries
- `def paginate`: 1 query
```

The INDEX is NOT materialised to disk — it's generated on demand by the
CLI from the current SQLite state. This keeps writes append-only and
avoids an extra moving part.

## Prompt-level integration

Brain's sub-agent dispatch prompt gets two additions:

1. **INDEX digest** — a ~500-token summary of what's already in the vault,
   listing keys (not content) so the sub-agent knows what facts exist.
2. **`search_facts(key)` tool** — exposed to sub-agents so they can
   retrieve a specific fact's full content by key. Returns the cached
   result as if they had just run the tool themselves.

Estimated saving on a sentry-006-scale case:
- Current: 7 agents × ~15 K tokens of tool output each = ~105 K tokens
- With vault: INDEX(500) + 3–4 × `search_facts` per agent (~2 K each) ≈ 45 K tokens
- **~57% prompt-token reduction**, plus the CPU savings from not re-running.

## Size management

- **Per-session cap**: 500 MB. LRU eviction when exceeded, weighted by
  build cost (tree-sitter graph evicts last, grep results first).
- **Content compression**: zlib on BLOB column, ~3–5× on read_file text.
- **Post-session cleanup**: hook in 9.17 deletes the SQLite file when
  Brain's synthesise step completes.
- **Orphan sweep**: startup scan of `~/.conductor/scratchpad/` removes any
  session DB > 24 h old (crash recovery).

## Lifecycle

```
  ┌──────────────────────────────────────────────┐
  │ PRBrainOrchestrator.run(pr)                  │
  │                                              │
  │   session_id = uuid4()                       │
  │   vault = FactStore.open(session_id)         │
  │                                              │
  │   # Phase 1-5 of PR Brain loop run here, all │
  │   # sub-agents dispatched with CachedToolExec │
  │                                              │
  │   try:                                       │
  │       result = synthesize(...)               │
  │   finally:                                   │
  │       vault.close()                          │
  │       # 9.17 hook fires here                 │
  │       lifecycle.emit("on_task_end", session) │
  │       # cleanup hook:                        │
  │       os.unlink(vault.path)                  │
  └──────────────────────────────────────────────┘
```

## MVP (first commit) vs full build

**MVP (this sprint)** — deliverable now:
- `backend/app/scratchpad/inflight.py` with `key_lock()` primitive
- `_ensure_graph` migrated to use `key_lock()` — fixes the sentry-006 stampede
- Unit tests for concurrency (7 threads, 1 build)

**Follow-up (Sprint 16, same 9.15 card)**:
- `backend/app/scratchpad/store.py` — SQLite-backed FactStore
- `backend/app/scratchpad/keys.py` — canonical key builders
- `CachedToolExecutor` wrapping `execute_tool`
- Range-intersection for `read_file`
- Negative cache + its integration with verify-existence (9.14b)
- INDEX dump CLI
- `search_facts` tool exposed to sub-agents

## Open questions

1. **Workspace path stability**: if the Brain runs on a symlinked or
   bind-mounted workspace, should the cache key use the resolved path?
   We think yes — resolve on ingress so all callers agree.
2. **Cache reuse across PR reviews of the same repo**: explicitly OUT of
   scope for 9.15 (that's long-term memory). But the SQLite file *could*
   be reused if we drop the session_id convention. Decision: keep strict
   per-session isolation for now; revisit when we build cross-PR learnings.
3. **What about tool results that are too large to cache?** Default cap:
   1 MB per fact. Results larger than that get cached as a pointer
   (file path + size) and the sub-agent re-reads on follower access.
   Rare; punt until we see it in practice.
