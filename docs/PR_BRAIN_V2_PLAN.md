# PR Brain v2 — Refactor Plan

Dated: 2026-04-19. Second revision after Phase 9.15 (Fact Vault) + 9.18
(scan hardening) shipped. This is not an incremental tweak to the fixed
7-agent swarm — it's a foundational redesign around a single thesis.

## Thesis — why "agent as tool" is the future

Future models will get stronger. Context windows will not grow fast
enough to match the problems we want to solve. Short-term memory (the
Fact Vault we just shipped, or anything like it) is itself *inside*
that context — memory doesn't extend the ceiling, it just repackages
what fits under it.

The only architectural direction that scales past this ceiling is
**task decomposition**: a strong thinking model (Brain) plans
investigations, weaker execution models (sub-agents) explore narrow
slices, each returns **distilled findings** instead of raw exploration.
Findings are far denser than the raw `grep` / `read_file` bytes that
produced them. Brain accumulates findings across many sub-agent calls
and reasons over the condensed picture.

The insight isn't that sub-agents are smart — it's that they compress
the environment into evidence. Brain is the thinking machine; agents
are its tools for seeing.

This is the same pattern Claude Code shipped as **coordinator mode**.
The reference code under `reference/claude-code/coordinator/` +
`reference/claude-code/tools/AgentTool/` is our direct intellectual
ancestor. We're building the equivalent with PR review as the first
concrete instance.

### Foundational assumptions

These are non-negotiable — if either breaks, the architecture breaks.

1. **Brain is smart enough.** Opus / Sonnet class. Specifically, smart
   enough to plan good investigations, choose scopes that actually
   contain the evidence, and classify severity using the 2-question
   rubric without drift across a review. This is the assumption we
   validated in 9.18 step 3's sentry-007 result — when Brain sees the
   real files, it catches the real bug on the first try.
2. **Sub-agent obeys scope.** Haiku / Sonnet class. Must not wander
   outside the declared scope, must answer the exact checks asked, must
   not invent severity classifications. The "verify-existence" rule
   we've been pushing into skills (grep before flagging) is part of
   this obedience contract.

If either assumption becomes empirically false, we fall back to the
fixed swarm. We keep the old `dispatch_agent` path intact during rollout
(see Checkpoint A) to make the fallback a config flag flip, not a
rewrite.

## Why v1 fails at this thesis

`PRBrainOrchestrator` in `backend/app/agent_loop/pr_brain.py` runs a
6-phase deterministic pipeline:

```
Phase 1 (deterministic): parse_diff → classify_risk → prefetch_diffs → impact_graph
Phase 2 (LLM fan-out):   7 fixed-role agents each get the full diff + full impact
                         context + their role's skill. Each decides what to
                         investigate AND classifies severity
Phase 3 (deterministic): evidence_gate → post_filter → dedup → rank
Phase 4 (LLM):           standalone arbitrator rebuts each finding
Phase 5 (deterministic): merge_recommendation
Phase 6 (LLM):           synthesis = final judge
```

Structural failures:

- **Role is the wrong axis.** "Correctness agent" and "security agent"
  both need to look at the same file for a given change. Fixed roles
  force the same files to be re-read in 7 parallel contexts (mitigated
  partially by 9.15's Fact Vault, but not eliminated).
- **Severity in 7 places is not severity.** Three concurrency-agent
  Haikus can independently flag the same code with three different
  severities. Arbitration can't fix this — it can only rebut, not
  unify. Sentry-007 step 1 showed three agents converging on a
  *hallucinated* null deref while missing the real bug — severity
  consensus on a false finding.
- **Brain doesn't direct attention.** It just picks which 7 roles to
  run. It never says "investigate `sessions.py:440-448` for thread
  safety". The real bug in requests-007 is a specific line range;
  fixed-role dispatch has no mechanism to point at it.
- **Unexpected observations are lost.** A correctness agent that
  notices a side-finding outside its role has nowhere to escalate it.

## What v2 does

A 5-phase coordinator loop. Brain **plans**, workers **execute**, Brain
**classifies**. One model doing severity across all findings with full
cross-cutting context. Scope-addressed investigations, not role-addressed.

```
Phase 1: Survey     Brain (Sonnet) reads diff + uses read-only tools
                    (grep, find_symbol, read_file, file_outline) to map
                    change points + risk surface. ≤100K tokens.
                    For each changed region, asks:
                      - what's the intent?
                      - what class of failure if wrong?
                      - what assertions rule those failures out?

Phase 2: Plan       Brain decomposes the survey into concrete
                    investigations. Each investigation is one
                    dispatch_subagent call with:
                      - narrow scope (≤5 files — REVISED from ≤3)
                      - exactly 3 falsifiable checks
                      - success_criteria
                      - budget
                      - model_tier (usually Haiku, sometimes Sonnet)
                      - may_subdispatch (default false — see recursion)

                    Hard invariants (prevent under-exploration):
                      - ≥1 correctness investigation per PR
                      - auth/crypto/session diffs → mandatory security dispatch
                      - DB migrations → mandatory reliability dispatch
                      - ≤8 dispatches total in one Brain turn
                      - ≤3 checks per dispatch
                      - Max recursion depth 2 (see "Recursion" below)

Phase 3: Execute    Parallel dispatch_subagent. Each worker returns:
                      {
                        checks: [{verdict: confirmed|violated|unclear, evidence}],
                        findings: [{severity: null, title, file, line, ...}],
                        unexpected_observations: [{confidence, ...}]
                      }
                    Workers NEVER classify severity. They NEVER
                    investigate outside scope. They may sub-dispatch
                    ONLY if the parent set may_subdispatch=true AND the
                    parent itself is at depth 1 (see Recursion).

Phase 4: Replan     Brain reacts to:
                      - unclear verdicts → dispatch a strong-model
                        follow-up investigation
                      - high-confidence unexpected_observations
                        (≥0.8) → dispatch a NEW investigation with a
                        new scope (may be entirely different files)
                    Up to 2 replan rounds. Still bounded by the total
                    8-dispatch cap for the Brain turn.

Phase 5: Synthesis  Brain dedups findings across all dispatches (its
                    own + workers' + sub-workers'), classifies severity
                    using the 2-question rubric (provable? + blast
                    radius?) with full cross-cutting context across
                    all findings, and emits the final review. The
                    standalone arbitrator is folded into this phase —
                    Brain may fork a strong-model verifier (9.16 Forked
                    Agent Pattern) for findings whose evidence is thin.
```

## Recursion model (revised)

Three levels allowed:

```
depth 0:  Brain (Sonnet)
          - plans investigations
          - may dispatch up to 8 sub-agents in one turn
depth 1:  Sub-agent (Haiku or Sonnet)
          - answers narrow checks within its scope
          - MAY dispatch its own sub-agents IFF the parent Brain set
            may_subdispatch=true AND the check genuinely warrants
            subdivision (e.g. "verify 3 separate call-site behaviors
            inside this file cluster")
          - still bounded by its own budget
depth 2:  Sub-sub-agent (Haiku)
          - answers even narrower checks
          - CANNOT dispatch further — hard wall, enforced at the
            AgentToolExecutor level via a ContextVar depth counter
            that rejects dispatch calls when depth >= 2
```

The depth-2 wall is what keeps the system tractable. Without it, a
single misbehaving Haiku can fan out exponentially.

**Why allow depth 1 → depth 2 at all?** Because sometimes a sub-agent's
check is itself a mini-investigation ("for each of the 3 call sites of
`foo`, verify that the caller handles the new None return"). Forcing
the sub-agent to answer that linearly on a 5-file scope burns its
budget. Letting it dispatch 3 narrow depth-2 sub-agents in parallel is
how you actually get through it.

## Where the intellectual debt is — study before code

Before implementation, read these:

- `reference/claude-code/tools/AgentTool/` — their coordinator's
  dispatch machinery; especially `forkSubagent.ts` and `resumeAgent.ts`.
  Our depth-2 verifier in synthesize should fork (inherit Brain's full
  context for cache-identical replay) rather than spawn fresh.
- `reference/claude-code/coordinator/coordinatorMode.ts` — the gating
  pattern. Coordinator mode is mutually exclusive with fork mode in
  their code. For us, Brain is always coordinator; fork is reserved
  for the depth-2 verifier inside synthesize.
- `reference/claude-code/skills/` — bundled skills vs disk-loaded
  skills (`loadSkillsDir.ts`). We should copy this dual-load pattern
  (see "Harness restructure" below).
- `reference/claude-code/tools/AgentTool/prompt.ts` — how `whenToUse`
  is injected. Our existing `config/agents/*.md` have role-shaped
  descriptions; v2 rewrites them as "when this kind of investigation
  is appropriate" so they work as examples Brain reads when planning.

These are references, not cargo cult. The adaptation layer matters.

## Harness restructure

The user's proposed layout and the Claude Code pattern align well. The
end state is:

```
config/
├── skills/                          ← ALL skills, one directory
│   ├── bundled/                     ← ship-with-product skills
│   │   ├── pr_brain_coordinator.md  ← the meta-skill that drives v2
│   │   ├── pr_subagent_checks.md    ← the worker contract
│   │   ├── ai_summary.md            ← existing summary skill, relocated
│   │   ├── code_review_pr.md        ← existing review skill, relocated
│   │   └── …
│   └── disk/                        ← per-project overrides (future)
│
├── tools/                           ← ALL tool prompts + schemas
│   ├── grep/
│   │   ├── prompt.md                ← tool description for LLM
│   │   └── schema.yaml              ← Pydantic-compatible param schema
│   ├── read_file/
│   │   ├── prompt.md
│   │   └── schema.yaml
│   ├── dispatch_subagent/           ← NEW in v2
│   │   ├── prompt.md
│   │   └── schema.yaml
│   └── … (43 tools, each in its own folder)
│
├── brains/                          ← brain-level prompts
│   ├── main.md                      ← the general query-answering Brain
│   ├── pr_review.md                 ← the PR brain (thin — just invokes
│   │                                  skills/bundled/pr_brain_coordinator)
│   └── …
│
└── agents/                          ← [DEPRECATED in v2, kept as
                                        reference material only]
```

**Programmer workflow after restructure**:

1. **Add a new skill**: drop a `.md` file in `config/skills/bundled/`.
   Content: when this skill applies, what it does, what tools it
   expects the agent to have, example queries it handles well.
2. **Add a new tool**: create `config/tools/{tool_name}/` with
   `prompt.md` (description + when-to-use + pitfalls) + `schema.yaml`
   (param types + constraints). Python implementation in
   `backend/app/code_tools/tools.py` remains the runtime entry.
3. **Teach Brain when to use the new skill**: add 1–3 examples to
   `config/brains/main.md` or `config/brains/pr_review.md` in the
   existing "Example dispatches" block.

No touching Python code for conceptual changes. Only touch Python when
the tool's runtime behavior changes (new param, new logic, new output
shape).

This is the Claude Code pattern adapted to our setup. `loadSkillsDir.ts`
equivalent in Python is straightforward — a single loader that scans
`config/skills/bundled/` at startup + any project-local directory.

### Migration path for the harness

- **Step 0 (one commit)**: `mkdir config/skills/bundled`,
  `config/tools/`, `config/brains/`. Move existing files into the new
  layout; update the loaders in `backend/app/workflow/loader.py`.
  Pure refactor, no behavior change; eval regression must stay flat.
- **Step 1**: add `dispatch_subagent` under `config/tools/` with its
  prompt + schema, register in Python.
- **Step 2**: add `pr_subagent_checks` and `pr_brain_coordinator`
  skills under `config/skills/bundled/`.
- **Step 3 (Checkpoint A)**: wire everything together, ship opt-in.
- **Step 4 (Checkpoint B)**: switch default, retire old agents/* as
  active dispatch targets.

## Concrete code changes

### Backend: `backend/app/agent_loop/pr_brain.py`

- `PRBrainOrchestrator.__init__` — add `meta_skill` parameter. When
  set, runs the v2 coordinator loop. When unset, falls back to v1
  fixed-swarm pipeline (rollback safety).
- `_survey()` (new) — Brain LLM call with read-only tools, returns
  structured survey output (change points + risk notes per change).
- `_plan()` (new) — LLM call, consumes survey, emits a list of
  `Investigation(scope, checks, success_criteria, budget, model_tier,
  may_subdispatch)`.
- `_execute()` (new) — wraps `dispatch_subagent` calls in parallel,
  collects worker responses + accumulates findings + unexpected
  observations.
- `_replan()` (new) — decides whether to dispatch more investigations
  based on unclear verdicts + unexpected observations (≥0.8
  confidence); bounded by dispatch budget + round count (max 2).
- `_synthesize_v2()` (new) — dedup + severity classification + forked
  verifier dispatch for thin-evidence findings + final review.

### Backend: `backend/app/agent_loop/brain.py`

- `AgentToolExecutor` — new `dispatch_subagent` tool. Signature:
  ```python
  dispatch_subagent(
      scope: list[str],          # file paths, max 5 (revised)
      checks: list[str],         # falsifiable questions, exactly 3
      success_criteria: str,     # what "confirmed" means
      budget: int,               # max iterations
      model_tier: str,           # "haiku" | "sonnet"
      may_subdispatch: bool,     # default False; true allows depth-2
  ) -> SubagentResponse
  ```
- Depth tracking via a `ContextVar`:
  - Brain dispatch → child runs at depth 1
  - depth-1 child dispatch (only if parent set `may_subdispatch=true`)
    → grandchild runs at depth 2
  - depth-2 child dispatch → rejected at executor level with a clear
    error

### Config: new layout under `config/skills/bundled/`

- `pr_subagent_checks.md` — the worker system prompt. Detection-only,
  no severity classification, verify-existence rule, scope restriction,
  exit with the checks+findings+unexpected schema.
- `pr_brain_coordinator.md` — the Brain meta-skill. Describes the
  5-phase loop, the dispatch contract, the hard invariants, the
  severity rubric. Contains ≥5 worked examples of good
  decomposition / dispatch plans.
- `code_review_pr.md`, `ai_summary.md`, … — existing skills relocated
  (plain file move; loader updated to the new path).

### Tool schemas: `backend/app/code_tools/schemas.py`

- Add `DispatchSubagentParams` Pydantic model. Validation at the
  schema layer:
  - `scope` length ≤ 5
  - `checks` length == 3
  - `success_criteria` non-empty
  - `model_tier` in {haiku, sonnet}
- Register in `TOOL_DEFINITIONS` + `TOOL_METADATA`.
- Only exposed to Brain + (conditionally) depth-1 sub-agents.

## Rollout — two checkpoints

### Checkpoint A (Sprint 16/17) — primitive + parallel availability

Lands `dispatch_subagent` + the new sub-agent schema alongside the
existing fixed swarm. Both paths live; v2 is opt-in via config flag.

- `dispatch_subagent` tool + schema + Pydantic params + depth tracker
- `pr_subagent_checks` skill under `config/skills/bundled/`
- Brain synthesize path branches: if any finding carries
  `severity: null` (new-schema worker), classify via Brain rubric;
  else pass through the v1 finding's severity.
- Verify-existence rule wired into the new worker skill.
- Harness restructure Step 0 (file layout migration, behavior flat).
- Side-by-side eval: `dispatch_subagent`-only vs fixed-swarm on
  - 12 requests cases
  - Greptile sentry-001..sentry-007 subset
  - Greptile grafana / discourse / keycloak subset (broader languages)

Acceptance:
- `dispatch_subagent` works end-to-end (depth 0 → 1 → 2 all exercised)
- Brain severity classification matches or exceeds fixed-swarm
  `severity_accuracy` on 12 requests cases
- Harness restructure passes full regression (1777+ tests)

### Checkpoint B (Sprint 18) — switch default, retire swarm

- `pr_brain_coordinator.md` becomes Brain's default system prompt for
  PR review flow
- Hard invariants enforced in code (min correctness, trigger patterns,
  max 8 dispatches, max depth 2, ≤5 files per scope, exactly 3 checks)
- `config/agents/*.md` carry a "reference only, dispatch via
  coordinator" banner
- Standalone arbitrator prompt retired; logic folded into synthesize
- Fixed swarm → fallback only, wrapped with a deprecation log

Acceptance:
- composite within ±1pp of Checkpoint A
- severity_accuracy 0.583 → 0.75+
- judge avg 2.2 → 3.0+
- token cost -30%+ vs fixed swarm

## Test plan

### Unit tests (alongside each change)

- `test_dispatch_subagent_scope_enforced.py` — worker rejects reads
  outside declared scope
- `test_dispatch_subagent_no_severity.py` — worker output has
  `severity: null` on every finding
- `test_dispatch_subagent_verify_existence.py` — worker refuses to
  flag logic on symbols it didn't grep/find_symbol first
- `test_dispatch_subagent_depth_wall.py` — depth-2 agent dispatch is
  rejected; depth-1 allowed only when parent opted in
- `test_brain_severity_classification.py` — Brain's 2-question rubric
  applied to sample finding batches, verdicts stable across rounds
- `test_pr_brain_v2_invariants.py` — min correctness dispatch, auth
  triggers security, migrations trigger reliability, max 8 dispatches,
  max depth 2, ≤5 files, exactly 3 checks
- `test_harness_loader.py` — `config/skills/bundled/` discovered;
  missing skill file raises friendly error; tool schema loaded from
  `config/tools/{name}/schema.yaml`

### Integration / eval

- Existing 12 requests cases — regression floor (composite ±2pp)
- Greptile sentry-001..007 — bug detection recall vs baseline
- Greptile grafana / discourse / keycloak / cal.com subset — broader
  language + framework coverage
- Langfuse traces: dispatch graph visible per PR, ≤8 dispatches, depth
  never exceeds 2, finding provenance traceable back to investigation

### Parity tests

- `dispatch_subagent` is backend-only (no TS side), but the 27
  TS-dispatched tools must stay parity-covered. Re-run the 160-test
  parity suite after each checkpoint. Any harness restructure that
  breaks parity is an automatic revert.

## Dependencies — all shipped

- **9.15 Fact Vault** — ✅ shipped. Sub-agents can grep / read_file
  the same files without paying 7× — `CachedToolExecutor` serves
  subsequent agents from the vault.
- **9.18 subprocess parse pool** — ✅ shipped. Brain's survey phase
  calls `_ensure_graph` / `_get_symbol_index`; both are bounded on
  pathological TSX via SIGKILL + heuristic.
- **9.16 Forked Agent Pattern** — Checkpoint B prerequisite. The
  depth-2 verifier inside synthesize should use the fork pattern
  (inherit Brain's exact context) rather than spawn fresh — avoids a
  full cache write per verifier. Can land separately.

## Order of operations

1. **Harness restructure step 0** (pure file move, loader update) —
   lowest risk, gets the layout in place
2. Write `dispatch_subagent` Pydantic schema + tool stub under
   `config/tools/dispatch_subagent/`
3. Wire into `AgentToolExecutor` with scope enforcement + depth
   ContextVar
4. Unit tests for scope, schema, verify-existence, depth wall
5. Write `pr_subagent_checks.md` under `config/skills/bundled/`
6. Brain synthesize path — severity classification branch
7. **Checkpoint A eval** — 12 requests + sentry subset + broader
   languages
8. Write `pr_brain_coordinator.md`
9. v2 orchestrator — `_survey` / `_plan` / `_execute` / `_replan` /
   `_synthesize_v2`
10. Hard invariant enforcement + unit tests
11. **Checkpoint B eval** — full comparison, cost, severity

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| v2 misclassifies severity worse than v1 | Checkpoint A gating eval — severity_accuracy must not drop, or we don't flip the default |
| Brain's plan phase hallucinates investigations outside the diff | Hard invariants cap dispatch count + trigger patterns always fire for auth/migration |
| Replan rounds explode budget | Max 2 replan rounds + total 8-dispatch cap enforced at executor |
| Workers break "no severity" contract | Pydantic schema rejects non-null severity; tests assert |
| Depth-2 workers fan out exponentially | ContextVar depth wall at executor layer; rejected with clear error; test covers |
| Harness restructure breaks skill/tool loading in production | Step 0 is a file move + loader change with flat eval regression; full parity + 1777 test suite must pass |
| v1 users on main break during rollout | `meta_skill` config flag keeps v1 path alive until Checkpoint B |

## Not in scope

- Changing any other pipeline (summary, Teams bot, Jira agent) — v2
  is PR-review-only for now, though the harness restructure benefits
  those pipelines too
- Frontend changes — the UI reads the same `ReviewResult` shape
- Webhook changes — Azure DevOps still calls
  `POST /api/integrations/azure_devops/webhook`, sees the same
  response shape
- Long-term cross-session memory — that's a Phase 9.17 follow-up,
  not part of v2

## One-paragraph summary

PR Brain v2 retires the fixed-role swarm for a coordinator pattern.
Brain surveys the diff, plans narrow investigations (≤5 files,
exactly 3 checks each), dispatches Haiku workers that return distilled
findings without severity, optionally replans based on unexpected
observations, then synthesizes with unified cross-cutting severity
classification. Recursion is capped at depth 2 and 8 dispatches per
Brain turn. Skills, tool prompts, and Brain prompts are reorganized
into `config/skills/bundled/`, `config/tools/{name}/`,
`config/brains/` so adding a new capability is a file drop + a Brain
example, not a Python refactor. Ships in two checkpoints: A lands the
primitive alongside the old swarm (opt-in); B switches the default
and retires the arbitrator. Foundations already in main: Fact Vault
(9.15), subprocess parse pool (9.18).
