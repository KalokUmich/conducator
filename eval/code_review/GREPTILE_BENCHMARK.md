# Greptile Benchmark — Local Setup & Eval Guide

A reproduction of [Greptile's public AI Code Review Benchmark (2025)][greptile-benchmarks]
inside our own eval harness, so we can score Conductor's PR Brain against the same
50 PRs that Greptile uses to compare itself with Cursor / Copilot / CodeRabbit / Graphite.

> **Original report**: <https://www.greptile.com/benchmarks>
> Read this first — it has the full methodology, the leaderboard, and the
> per-case bug descriptions for Sentry. The other 4 repos' tables are
> JS-rendered and only visible in a real browser.
>
> **Sources of every PR in the benchmark**: the 25 fork repos under
> <https://github.com/orgs/ai-code-review-evaluation/repositories>
> (5 target repos × 5 reviewer tools — same 10 PRs in each tool fork).

---

## 1. What this benchmark tests

Greptile took **50 real-world bug-fix PRs** from 5 popular OSS projects, traced
each fix back to the commit that introduced the bug, and re-created the buggy
state on a clean fork. They then ran 5 commercial AI code reviewers against
each PR with default settings, and scored each tool on whether it left an
**explicit line-level PR comment that points to the faulty code and explains
the impact**.

That binary "did the reviewer catch the planted bug" is the **catch rate**
metric — Greptile's headline number. Their own published numbers (July 2025):

| Tool         | Overall catch rate |
|--------------|-------------------:|
| **Greptile** |              82 % |
| Cursor       |              58 % |
| Copilot      |              54 % |
| CodeRabbit   |              44 % |
| Graphite     |               6 % |

We mirror the **catch rate** metric (`scorer.py::CaseScore.catch_rate`) so we
can speak Greptile's language directly. See §6 for how to interpret it.

### Dataset breakdown

| Target repo  | Language     | Cases (auto + manual) | Sample bug types                              |
|--------------|--------------|----------------------:|-----------------------------------------------|
| sentry       | Python       |                9 + 1 | OAuth state, paginator NPE, exit-code break   |
| cal.com      | TypeScript   |                9 + 1 | async forEach pattern, dynamic import         |
| grafana      | Go / TS      |                8 + 2 | RBAC negative cache, Loki query interpolation |
| keycloak     | Java         |                9 + 1 | exit-code contract change, feature flagging   |
| discourse    | Ruby / SCSS  |                9 + 1 | float-on-flexbox layout, image downsizing     |
| **TOTAL**    |              |                **50** |                                              |

* **44 auto cases** are imported from inline `logic:` / `security:` /
  `performance:` / `syntax:` review comments left by the `greptile-apps[bot]`
  account on each PR (see `import_greptile.py`).
* **6 manual cases** are hand-annotated because Greptile's bot either
  (a) only left style nits, (b) only left a top-level summary review with
  no inline anchors, or (c) didn't review the PR at all (free-trial expired).
  See `cases/greptile_*/manual_cases.yaml` — each case is marked
  `quality: human` and the bug is identified by reading the diff against the
  base SHA.

### What we explicitly DO measure

* **catch_rate** — did PR Brain emit a finding that matches the planted bug
  on `(file, line)`? This is the headline metric, comparable to Greptile's 82 %.
* **recall** — for cases with multiple expected findings (e.g. a sentry PR
  that plants 3 bugs), what fraction did we catch?
* **precision** — what fraction of our findings match an expected one?
  ("False positives" here means findings that don't map to the planted bug —
  but they may still be real defects.)
* **severity_accuracy** — did we label the bug as critical / warning / nit
  the way Greptile did?
* **LLM judge** (`judge.py`) — qualitative assessment of detection,
  reasoning_quality, actionability, false_positive_discipline. Strict 1/3/5
  rubric, see `judge.py` for the full prompt.

### What we DON'T measure (and why)

* **Latency / cost** — out of scope. Greptile's benchmark doesn't compare these
  either, even though they matter operationally.
* **Multi-reviewer consensus** — we score one reviewer (PR Brain) at a time.
* **Style / naming nits** — explicitly excluded by the `code_review_pr` skill's
  `DO NOT FLAG` list.

---

## 2. Where the data comes from

```
                  Greptile maintains              We commit to repo            Local-only (gitignored)
                  ──────────────────              ─────────────────            ────────────────────────
greptile.com/  ┐
 benchmarks    │   ai-code-review-evaluation/         cases/greptile_<target>/    repos/<target>-greptile/
 (the         ──>  {sentry,cal.com,grafana,    ──>   ├─ cases.yaml         ──>  (git clone, ~2 GB total)
  scoreboard) │     keycloak,discourse}-greptile     ├─ manual_cases.yaml
               ┘    (5 fork repos × 10 PRs)          └─ patches/*.patch         repos/greptile_bases/
                                                                                <target>/<NNN>/
                                                                                (git archive of merge-base,
                                                                                 ~6 GB total)
```

* **The fork repos** (`ai-code-review-evaluation/{target}-greptile`) hold the
  10 test PRs as ordinary GitHub PRs. The `greptile-apps[bot]` reviewed each
  one inline; those review comments are our auto-import source of truth.
* **`cases/greptile_*/cases.yaml`** is auto-generated by `import_greptile.py`
  from the scraped review comments. Committed to the repo as the source of
  truth for "what's the bug, where, what severity".
* **`cases/greptile_*/manual_cases.yaml`** holds the 6 hand-annotated cases
  (sentry-004, discourse-005, keycloak-004, grafana-002, grafana-004,
  cal_com-002). Also committed.
* **`cases/greptile_*/patches/*.patch`** is the unified diff between the
  merge-base SHA and the head SHA, regenerated locally to apply cleanly
  against the materialized base. Committed.
* **`repos/<target>-greptile/`** are full git clones of each fork — local-only,
  gitignored. The setup script clones them on first run.
* **`repos/greptile_bases/<target>/<NNN>/`** are per-case `git archive`
  snapshots of the merge-base commit — also local-only, gitignored. The
  setup script extracts them on first run.

---

## 3. Quick start (default mode)

### Prerequisites

* `git` on PATH
* `python3` ≥ 3.10
* `~12 GB` free disk
* AWS Bedrock credentials (for the eval itself, not for setup) —
  `eu.anthropic.claude-sonnet-4-6` and `eu.anthropic.claude-haiku-4-5-20251001-v1:0`
  must be enabled in your AWS region.

### Setup (one-time, ~5 min)

```bash
cd backend  # so PYTHONPATH picks up app.*
python ../eval/code_review/setup_greptile_dataset.py
```

This is the **default mode** (Layer C only — see §5 for the layer model). It:

1. Clones the 5 fork repos anonymously to `eval/code_review/repos/<target>-greptile/`.
   These are public repos, so **no GitHub token required**.
2. For each of the 50 cases, computes `merge-base(base_sha, head_sha)` and
   extracts that snapshot via `git archive` into
   `eval/code_review/repos/greptile_bases/<target>/<NNN>/`.
3. Regenerates the patches locally as `git diff merge_base..head_sha` so they
   apply cleanly against the materialized snapshot. (See §7 for why this
   matters more than you'd think.)

You'll know it worked when you see something like:

```
Done. materialized=50 patches_regenerated=50 skipped=0 errored=0
```

### Run the eval

```bash
cd backend
python ../eval/code_review/run.py --brain \
    --provider bedrock \
    --model "eu.anthropic.claude-sonnet-4-6" \
    --explorer-model "eu.anthropic.claude-haiku-4-5-20251001-v1:0" \
    --filter greptile- \
    --verbose
```

* `--brain` routes through `PRBrainOrchestrator` (the production code path)
  rather than the legacy `CodeReviewService`.
* `--filter greptile-` runs only the 50 Greptile cases (drop the flag to also
  include the 12 legacy `requests` cases).
* `--verbose` prints per-finding match results so you can see which
  expected_findings each agent caught.
* Drop `--no-judge` to enable the LLM judge for qualitative scoring.

The eval takes **~3-4 hours** for all 50 cases (Sonnet brain + 7 Haiku
sub-agents per case + judge). Run on a smaller filter (`--filter greptile-sentry`)
first to confirm the pipeline works end-to-end before committing to the
full run.

---

## 4. Reading the report

```
Per-Case Scores:
Case                  Catch   Recall     Prec      Sev      Loc      Rec      Ctx     Comp
------------------------------------------------------------------------------------------------
greptile-sentry-001       Y    1.000    0.800    0.500    0.500    0.000    1.000    0.735
greptile-sentry-002       Y    1.000    0.667    1.000    1.000    0.667    1.000    0.823
greptile-sentry-004       .    0.000    0.000    0.000    0.000    0.000    0.000    0.000
...

Aggregate             0.940    0.876    0.762    0.500    0.762    0.595    1.000    0.751

Catch rate (Greptile-style): 47/50 = 94.0%
```

The columns:

| Column | Meaning |
|---|---|
| `Catch` | `Y` = at least one expected finding matched on file+line. `.` = missed. **This is the headline metric.** |
| `Recall` | Fraction of *all* expected findings the reviewer caught (matters for multi-bug cases). |
| `Prec` | Fraction of the reviewer's findings that match an expected one (extras count as false positives, lightly weighted). |
| `Sev` | Severity-label accuracy on matched findings. |
| `Loc` | File + line accuracy on matched findings. |
| `Rec` | Recommendation/fix-text similarity to the expected one. |
| `Ctx` | Did the reviewer touch the cross-file context the case requires? |
| `Comp` | Composite score (recall 35% / prec 20% / sev 15% / loc 10% / rec 10% / ctx 10%). |

The **Catch rate (Greptile-style)** line at the bottom is the number you
compare to Greptile's published 82 %.

### LLM judge verdicts (when enabled)

```
LLM Judge Verdicts
Case                    Compl   Reason   Action       FP      Avg
------------------------------------------------------------
greptile-sentry-001         5        4        5        4     4.55
greptile-sentry-002         5        5        5        5     5.00
greptile-sentry-004         1        1        1        1     1.00
```

The judge uses **strict 1/3/5 anchors** for `completeness` and `actionability`
(no fuzzy 2/4 — see `judge.py`'s system prompt). The four columns are:

| Column | Scale | Meaning |
|---|---|---|
| `Compl` (completeness) | 1 / 3 / 5 | Did we find the planted bug? 1 = missed, 3 = noticed but wrong line/severity/category, 5 = clean catch. |
| `Reason` (reasoning_quality) | 1–5 | Is the evidence chain verifiable from the cited code alone? 4–5 require an independently re-derivable explanation. |
| `Action` (actionability) | 1 / 3 / 5 | Is the suggested fix copy-pasteable? 1 = no fix, 3 = direction named but vague, 5 = concrete patch at the right line. |
| `FP` (false_positive_discipline) | 1–5 | Penalty for noise on secondary findings — 1 = 4+ irrelevants or any DO-NOT-FLAG violation, 5 = no extras or all extras are real bugs. |

Weighted average: completeness 40 % / reasoning 25 % / fp 20 % / action 15 %.

---

## 5. Refresh modes

You will basically never need these in normal use. Read this section only when:

* You tweak the `import_greptile.py` heuristics and want the cases.yaml to reflect them
* Greptile announces an update to their public benchmark
* Something is broken on disk

### Layer A — Re-scrape from GitHub

```bash
GITHUB_TOKEN=ghp_... python ../eval/code_review/setup_greptile_dataset.py --refresh-scrape
```

**When**: Greptile updates their fork repos. New PRs added (50 → 60), branches
force-pushed, bot reviews re-run, etc. Probably ≤ 1× per quarter.

**Cost**: ~150 GitHub API calls. Anonymous quota is 60/hour, so a token is
mandatory. A fine-grained PAT with **public_repo:read** scope is enough —
see <https://github.com/settings/personal-access-tokens>.

**Side effect**: `cases/greptile_*/cases.yaml` and `cases/greptile_*/patches/`
will show as modified in `git status`. Review the diff and commit.

### Layer B — Re-import from cached scraped JSON

```bash
python ../eval/code_review/setup_greptile_dataset.py --refresh-import
```

**When**: You changed the import logic (severity inference regex, line_range
window, category mapping, title_pattern token extraction, …) and want the
cases.yaml regenerated.

**Cost**: <1 min, no token. Reads `cases/greptile_raw/*.json` (cached scrape
output) and rewrites `cases/greptile_<target>/cases.yaml`.

**Side effect**: cases.yaml will show modified. Manual cases (in
`manual_cases.yaml`) are not touched.

### Layer C — Re-clone forks + re-materialize bases

This is the **default** mode (no flags). Run it after `git pull` brings new
case entries from a teammate, or if your local `repos/greptile_bases/` got
corrupted.

```bash
python ../eval/code_review/setup_greptile_dataset.py
python ../eval/code_review/setup_greptile_dataset.py --force         # re-extract even if present
python ../eval/code_review/setup_greptile_dataset.py --skip-clone    # assume forks already cloned
```

---

## 6. Comparing to Greptile's published numbers — caveats

Our `catch_rate` and Greptile's published catch rate use the **same definition
of "caught"** (an explicit line-level finding pointing at the planted bug),
so the headline numbers are directly comparable. But several caveats apply:

### Caveat 1 — Training-data contamination

All 5 source repos (Sentry, Cal.com, Grafana, Keycloak, Discourse) are
massively popular OSS projects, and the bug-fix commits these PRs are derived
from were public well before the model training cutoff (Sonnet 4.6: ~2025-04,
Haiku 4.5: ~2025-04). Sonnet/Haiku may **remember the original fix** for some
of these bugs and "catch" them from training memory rather than reasoning.
The same caveat applies to every commercial AI reviewer in Greptile's
leaderboard, so this is a level playing field — but **don't read our catch
rate as an absolute capability claim**, only as a relative comparison.

### Caveat 2 — Greptile's bot is the source of truth

For the 44 auto-imported cases, the `expected_findings` are derived from
**inline review comments left by `greptile-apps[bot]`** on those PRs. In other
words: the ground truth is "where Greptile's own bot said the bug was". If
Greptile's bot pointed at the wrong line or split one bug into two, our
expected_findings inherit that bias.

### Caveat 3 — The 6 manual cases are educated guesses

For sentry-004, keycloak-004, grafana-002, grafana-004, and cal_com-002, the
planted bug is **clearly identifiable** from the diff (and matches the public
Greptile severity table where available). Confidence: high.

For discourse-005, the planted bug is **a best guess** — Greptile bot only
left style nits and the public table for discourse isn't visible in the
benchmark page's HTML. Confidence: medium. The reviewer that finds the
"floats inside flexbox" anti-pattern in `topic-post.scss` will be marked as
catching.

### Caveat 4 — Sample size

50 cases is statistically thin. The 95 % CI on a true 80 % catch rate at
n=50 is roughly ±11 percentage points (`±2 √(p·(1-p)/n)`). So we can
distinguish a 60 % reviewer from a 95 % one with confidence, but **we
cannot distinguish a 75 % reviewer from an 85 % reviewer** at this sample
size. Single-case wins/losses are noise; trends across 5 repos are signal.

### Caveat 5 — Default settings only

Greptile's methodology section explicitly notes: *"all tools ran with default
settings (no custom rules or fine-tuning)."* We follow the same constraint —
PR Brain runs with the production prompt set, no per-case tuning.

### What a fair comparison looks like

| Reviewer  | Greptile-published | Our run on the same dataset |
|-----------|-------------------:|----------------------------:|
| Greptile  |               82 % |                  N/A (theirs) |
| Cursor    |               58 % |                  N/A (theirs) |
| Copilot   |               54 % |                  N/A (theirs) |
| CodeRabbit|               44 % |                  N/A (theirs) |
| Graphite  |                6 % |                  N/A (theirs) |
| **Conductor PR Brain** |    — |  **(fill in after first full eval)** |

When you want to publish a number, run the full eval **3 times** and report
the median + range. LLM nondeterminism means a single run can swing
~5–10 percentage points.

---

## 7. How the data gets onto disk — the merge-base trick

This section is for the curious — you can skip it if you just want to run
the eval. It explains why `materialize_greptile_bases.py` is more
complicated than "git checkout base_sha".

The naive approach is:

1. Download the API-returned `.diff` for each PR.
2. Materialize a snapshot of `base_sha` (the commit the PR is based on).
3. Apply the diff. ✗

This **fails** for two reasons we hit in practice:

1. **GitHub's `.diff` is computed against `merge-base(base, head)`**, not
   against `base.sha`. When the base branch advances after the PR is opened,
   `base.sha` includes commits the diff is unaware of, and `git apply` fails
   with "patch does not apply" on the unrelated changes.
2. **The base branch in these forks tends to advance** because they're
   re-published periodically to track upstream. Sentry's `master` in the
   fork has moved many SHAs ahead of where most PRs were originally opened.

The fix:

1. Resolve `head_ref` → `head_sha` against the freshly-cloned fork.
2. Compute `merge_base = git merge-base base_sha head_sha`.
3. `git archive merge_base | tar -x` → that's the snapshot.
4. `git diff merge_base head_sha` → that's the patch.

Both come from the **same** local fork clone, so they're guaranteed
consistent and the patch always applies.

This is what `materialize_greptile_bases.py::process_target` does.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `git apply ... patch does not apply` | Base snapshot was extracted from the wrong SHA | `python setup_greptile_dataset.py --force` to re-extract |
| `git archive failed for ref=...` | Clone is stale (Greptile updated branches) | `rm -rf eval/code_review/repos/<target>-greptile && python setup_greptile_dataset.py` |
| `FATAL: GITHUB_TOKEN required` | Used `--refresh-scrape` without a token | Set `GITHUB_TOKEN=ghp_...` (fine-grained PAT, public_repo:read) |
| `Bedrock converse FAILED ... ExpiredTokenException` | AWS STS token expired mid-eval | Refresh credentials in `config/conductor.secrets.local.yaml` and re-run |
| `composite=0.000 (recall=0.00, findings=0)` on every case | All Bedrock calls failed | Check AWS credentials BEFORE the next run — see `~/.aws/credentials` or `conductor.secrets.local.yaml` |
| `setup_workspace` takes 60+ seconds | Sentry / Grafana bases are 13K-17K files; `shutil.copytree` is slow on WSL | Known cost. ~2 min per case on first run, faster after the OS page cache warms up. |
| Disk usage > 12 GB | You ran `--refresh-scrape` and have stale bases not cleaned up | `rm -rf eval/code_review/repos/greptile_bases && python setup_greptile_dataset.py` |

---

## 9. References

* **Greptile's benchmark report**: <https://www.greptile.com/benchmarks>
  — original methodology, leaderboard, per-case Sentry table.
* **Test PR forks**:
  <https://github.com/orgs/ai-code-review-evaluation/repositories>
  — 25 forks (5 targets × 5 reviewer tools), each with 10 open PRs.
* **Our scraper**: `eval/code_review/scrape_greptile.py`
* **Our importer**: `eval/code_review/import_greptile.py`
* **Our materializer**: `eval/code_review/materialize_greptile_bases.py`
* **Our setup wrapper**: `eval/code_review/setup_greptile_dataset.py`
* **Our scorer**: `eval/code_review/scorer.py` (computes catch_rate)
* **Our judge**: `eval/code_review/judge.py` (LLM-as-judge with strict rubric)
* **PR Brain (production code)**: `backend/app/agent_loop/pr_brain.py`
* **The 6 manual cases**: `cases/greptile_*/manual_cases.yaml`

[greptile-benchmarks]: https://www.greptile.com/benchmarks
