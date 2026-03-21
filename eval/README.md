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

Measures `CodeReviewService` quality against 12 planted-bug cases in `requests` v2.31.0.

```bash
cd backend

# Run all 12 cases
python ../eval/code_review/run.py --provider anthropic --model claude-sonnet-4-20250514

# Single case, no LLM judge
python ../eval/code_review/run.py --filter "requests-001" --no-judge

# Save baseline for regression detection
python ../eval/code_review/run.py --save-baseline

# Gold-standard ceiling
python ../eval/code_review/run.py --gold --gold-model opus --save-baseline
```

**Scoring**: recall (35%), precision (20%), severity (15%), location (10%), recommendation (10%), context (10%).

```
code_review/
├── run.py              CLI entrypoint
├── runner.py           Workspace setup + CodeReviewService execution
├── scorer.py           Deterministic scoring
├── judge.py            LLM-as-Judge qualitative evaluation
├── report.py           Report generation + baseline comparison
├── gold_runner.py      Gold-standard (single-agent) runner
├── repos.yaml          Repo manifest
├── repos/requests/     requests v2.31.0 source tree
├── cases/requests/     12 case definitions + patches
├── gold_baselines/     Gold-standard baselines
└── gold_traces/        Per-case gold agent traces
```

---

## 2. Agent Quality Eval (`agent_quality/`)

Measures agentic loop answer quality by running questions against real codebases and scoring answers against expected findings.

```bash
cd backend

# Run all baselines (direct agent, ~30s per case)
python ../eval/agent_quality/run.py

# Compare direct agent vs workflow (multi-agent)
python ../eval/agent_quality/run.py --compare

# Run specific case
python ../eval/agent_quality/run.py --case abound_render_approval

# Workflow only
python ../eval/agent_quality/run.py --workflow
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
