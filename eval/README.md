# Eval System

Three independent evaluation suites for measuring Conductor quality.

```
eval/
├── code_review/          Code Review pipeline quality (planted-bug cases)
├── agent_quality/        Agentic loop answer quality (baseline comparison)
└── tool_parity/          Python vs TS tool output comparison
```

---

## 1. Code Review Eval (`code_review/`)

Measures PR review quality across 4 suites (**42 cases**):
`requests` (12), `greptile-sentry` (10), `greptile-grafana` (10), `greptile-keycloak` (9).
Two modes: Brain pipeline (`PRBrainOrchestrator` v2, default) and gold-standard (`claude` CLI).

```bash
cd backend

# Brain pipeline (default)
python ../eval/code_review/run.py --brain --provider bedrock --model eu.anthropic.claude-sonnet-4-6 \
  --explorer-model eu.anthropic.claude-haiku-4-5-20251001-v1:0 --verbose
python ../eval/code_review/run.py --brain --filter "greptile-grafana-009" --no-judge --verbose

# Gold-standard ceiling (Claude Code CLI)
python ../eval/code_review/run.py --gold --gold-model sonnet --save-baseline

# Save baseline for regression detection
python ../eval/code_review/run.py --brain --save-baseline

# 4-suite parallel regression (convenience wrapper — see Makefile)
make eval-brain-regression TAG=v2r
```

**Scoring**: recall (35%), precision (20%), severity (15%), location (10%), recommendation (10%), context (10%).

**Flags**: `--brain` (default on, coordinator-worker pipeline), `--verbose` (per-finding match details), `--gold` (Claude Code CLI baseline), `--no-judge` (skip LLM judge), `--filter` (run subset).

```
code_review/
├── run.py              CLI entrypoint (brain + gold modes)
├── runner.py           Workspace setup + PRBrainOrchestrator execution
├── scorer.py           Deterministic scoring
├── judge.py            LLM-as-Judge qualitative evaluation
├── report.py           Report generation + baseline comparison
├── gold_runner.py      Gold-standard (Claude Code CLI) runner
├── repos.yaml          Repo manifest
├── cases/              Per-suite cases (requests + greptile_{sentry,grafana,keycloak})
├── repos/              Materialised source trees (hardlink-shared across cases)
├── gold_baselines/     Gold-standard baselines
└── gold_traces/        Per-case gold agent traces
```

---

## 2. Agent Quality Eval (`agent_quality/`)

Measures agentic loop answer quality by running questions against real codebases and scoring answers against expected findings.

```bash
cd backend

# Run all baselines (direct agent, ~30s per case)
python ../eval/agent_quality/run_bedrock.py

# Compare direct agent vs workflow vs brain
python ../eval/agent_quality/run_bedrock.py --all

# Brain orchestrator only
python ../eval/agent_quality/run_bedrock.py --brain

# Workflow only
python ../eval/agent_quality/run_bedrock.py --workflow
```

**Scoring**: pattern-match against `required_findings` in baseline JSON. Each finding has a weight and minimum pattern matches required.

### Adding a baseline case

Create a JSON file in `agent_quality/baselines/`:

```json
{
  "id": "unique_case_id",
  "workspace": "/path/to/codebase",
  "question": "The question to ask the agent",
  "baseline_model": "claude-opus-4-6",
  "thinking_steps": [ ... ],
  "answer": "The reference answer from Claude Code",
  "required_findings": [
    {
      "id": "finding_id",
      "description": "What must be found",
      "weight": 0.40,
      "check_patterns": ["regex1", "regex2"],
      "min_matches": 2
    }
  ]
}
```

```
agent_quality/
├── run.py              CLI entrypoint
├── baselines/          Baseline case definitions (JSON)
└── results.json        Latest run results
```

---

## 3. Tool Parity Eval (`tool_parity/`)

Compares tool output between Python (tree-sitter) and TypeScript (extension) implementations.

```bash
cd backend

# Generate Python baseline
python ../eval/tool_parity/run.py --generate-baseline

# Compare TS output against baseline (requires extension running)
python ../eval/tool_parity/run.py --compare
```

```
tool_parity/
├── run.py              Comparison script
└── baseline.json       Python tool output baseline
```
