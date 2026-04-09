"""LLM judge for agent_quality eval — replaces brittle pattern matching.

Why
---
The default ``score_answer()`` in ``run_bedrock.py`` checks ``re.search``
against a fixed list of regex patterns per finding. This punishes
semantically-correct answers that use different vocabulary than the
baseline writer chose. For example:

  baseline:  ``severity|score|risk``
  answer:    "DAG of FuncNode evaluated in dependency order ..."
  verdict:   MISS  (despite being a more accurate description)

This judge asks Claude Sonnet to make a semantic verdict per finding
(PASS / PARTIAL / MISS), then aggregates against the same weights.

Usage
-----
1. Run the eval as usual — it writes ``results_bedrock.json``.
2. Run this judge on that file:

       python eval/agent_quality/judge.py

   Optionally, ``--results <path>`` to point at a different results file
   (e.g. a per-run snapshot).

The output is a side-by-side comparison of pattern-match scores and
LLM-judge scores per case.

Determinism
-----------
The judge uses ``temperature=0`` so the same (answer, finding) pair
always yields the same verdict. This is essential — without it we
would just be adding judge noise on top of agent noise.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Make ``app.*`` importable when run from the repo root
backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
# Also add the eval/agent_quality dir so we can import run_bedrock as a module
_eval_dir = Path(__file__).resolve().parent
if str(_eval_dir) not in sys.path:
    sys.path.insert(0, str(_eval_dir))

from run_bedrock import _create_provider  # type: ignore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("agent_judge")


JUDGE_MODEL_ID = "eu.anthropic.claude-sonnet-4-6"

JUDGE_SYSTEM = """\
You are evaluating whether an AI's answer to a code-exploration question covers a specific required finding. Be strict on substance but lenient on vocabulary — semantically equivalent phrasing counts as covered.
"""

JUDGE_USER_TEMPLATE = """\
QUESTION the AI was asked:
{question}

REQUIRED FINDING:
- ID: {finding_id}
- Description: {finding_desc}

THE AI's ANSWER:
{answer}

TASK
Evaluate whether the answer adequately covers this finding. Output exactly:

VERDICT: PASS|PARTIAL|MISS
REASON: <one sentence>

Where:
- PASS    = the answer fully covers what the description requires (any vocabulary)
- PARTIAL = the answer touches the topic but misses important sub-aspects
- MISS    = the answer does not address this requirement at all

Important:
- Different vocabulary is OK. "DAG of FuncNode in dependency order" CAN cover "ML feature evaluation" if the underlying mechanism is what the description asks about.
- Implementation-level enum names (gambling, qcr_unsustainable) DO satisfy a requirement to "list decline reasons" — you don't need the answer to use business-level vocabulary.
- Be honest: if the answer skips an aspect entirely, mark MISS.
"""


VERDICT_SCORE = {"PASS": 1.0, "PARTIAL": 0.5, "MISS": 0.0}


def _parse_verdict(text: str) -> tuple[str, str]:
    """Parse the judge response into (verdict, reason)."""
    verdict = "MISS"
    reason = ""
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("VERDICT:"):
            v = line.split(":", 1)[1].strip().upper()
            for candidate in ("PASS", "PARTIAL", "MISS"):
                if candidate in v:
                    verdict = candidate
                    break
        elif line.upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
    return verdict, reason


def judge_one(
    provider,
    question: str,
    finding: dict,
    answer: str,
) -> dict:
    """Send one (finding, answer) pair to the judge and return verdict + score."""
    user = JUDGE_USER_TEMPLATE.format(
        question=question,
        finding_id=finding["id"],
        finding_desc=finding["description"],
        answer=answer,
    )
    response = provider.chat_with_tools(
        messages=[{"role": "user", "content": [{"text": user}]}],
        tools=[],
        system=JUDGE_SYSTEM,
        temperature=0.0,
    )
    text = (response.text or "").strip()
    verdict, reason = _parse_verdict(text)
    score = VERDICT_SCORE.get(verdict, 0.0)
    return {
        "id": finding["id"],
        "weight": finding["weight"],
        "score": score,
        "verdict": verdict,
        "reason": reason,
        "raw": text,
    }


def judge_case(
    provider,
    case_id: str,
    question: str,
    answer: str,
    required_findings: list[dict],
    parallel: bool = True,
) -> dict:
    """Judge a single case across all required findings, in parallel."""
    findings_out: list[dict] = [None] * len(required_findings)  # type: ignore[list-item]

    def _work(idx_finding: tuple[int, dict]) -> tuple[int, dict]:
        idx, finding = idx_finding
        return idx, judge_one(provider, question, finding, answer)

    if parallel:
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = [ex.submit(_work, (i, f)) for i, f in enumerate(required_findings)]
            for fut in as_completed(futures):
                idx, result = fut.result()
                findings_out[idx] = result
    else:
        for i, f in enumerate(required_findings):
            findings_out[i] = judge_one(provider, question, f, answer)

    # Compute weighted total
    total_weighted = sum(f["score"] * f["weight"] for f in findings_out)
    total_weight = sum(f["weight"] for f in findings_out)
    total = total_weighted / total_weight if total_weight > 0 else 0.0

    return {
        "case_id": case_id,
        "total_score": round(total, 3),
        "findings": findings_out,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _verdict_icon(v: str) -> str:
    return {"PASS": "PASS   ", "PARTIAL": "PARTIAL", "MISS": "MISS   "}.get(v, v)


def print_case_report(case_id: str, judged: dict, pattern_scoring: dict | None) -> None:
    print(f"\n{'='*70}")
    print(f"  {case_id}")
    print(f"{'='*70}")
    if pattern_scoring:
        print(f"  Pattern-match score: {pattern_scoring['total_score']*100:.1f}%")
    print(f"  LLM judge score:     {judged['total_score']*100:.1f}%")
    print()
    print(f"  {'finding':25s} {'weight':>7s}  {'pattern':>10s}  {'judge':>10s}  reason")
    print(f"  {'-'*25} {'-'*7}  {'-'*10}  {'-'*10}  {'-'*30}")
    pattern_by_id = {f["id"]: f for f in (pattern_scoring or {}).get("findings", [])}
    for jf in judged["findings"]:
        fid = jf["id"]
        weight = jf["weight"]
        pf = pattern_by_id.get(fid, {})
        pat_score = pf.get("score")
        pat_str = f"{pat_score*100:.0f}%" if pat_score is not None else "—"
        jud_str = _verdict_icon(jf["verdict"])
        print(
            f"  {fid:25s} {weight:>6.0%}   {pat_str:>10s}  {jud_str:>10s}  "
            f"{jf['reason'][:60]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM judge for agent_quality eval")
    parser.add_argument(
        "--results",
        default=str(Path(__file__).parent / "results_bedrock.json"),
        help="Path to results JSON written by run_bedrock.py",
    )
    parser.add_argument(
        "--baselines-dir",
        default=str(Path(__file__).parent / "baselines"),
        help="Directory containing baseline JSON files",
    )
    parser.add_argument(
        "--mode",
        default="brain",
        choices=["brain", "direct", "workflow"],
        help="Which mode in the results JSON to judge",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output JSON path for the judged scores",
    )
    args = parser.parse_args()

    results_path = Path(args.results)
    baselines_dir = Path(args.baselines_dir)

    if not results_path.is_file():
        logger.error("Results file not found: %s", results_path)
        return 1

    with open(results_path) as f:
        results = json.load(f)

    provider = _create_provider(JUDGE_MODEL_ID)
    logger.info("Judge model ready: %s", JUDGE_MODEL_ID)

    judged_all: dict = {}
    pattern_scores: list[float] = []
    judge_scores: list[float] = []

    for case_id, modes in results.items():
        case_data = modes.get(args.mode)
        if not case_data:
            logger.warning("Case %s has no '%s' mode, skipping", case_id, args.mode)
            continue

        run = case_data["run"]
        pattern_scoring = case_data.get("scoring", {})
        answer = run.get("answer", "")
        if not answer:
            logger.warning("Case %s has empty answer, skipping", case_id)
            continue

        baseline_path = baselines_dir / f"{case_id}.json"
        if not baseline_path.is_file():
            logger.warning("Baseline not found for %s", case_id)
            continue
        with open(baseline_path) as f:
            baseline = json.load(f)

        question = baseline["question"]
        required_findings = baseline["required_findings"]

        logger.info("Judging case: %s (%d findings)", case_id, len(required_findings))
        t0 = time.monotonic()
        judged = judge_case(provider, case_id, question, answer, required_findings)
        elapsed = time.monotonic() - t0
        logger.info("  done in %.1fs", elapsed)

        judged_all[case_id] = judged
        print_case_report(case_id, judged, pattern_scoring)

        pattern_scores.append(pattern_scoring.get("total_score", 0))
        judge_scores.append(judged["total_score"])

    # Summary
    print(f"\n{'='*70}")
    print("  SUMMARY  (pattern-match vs LLM judge)")
    print(f"{'='*70}")
    print(f"  {'case':30s} {'pattern':>10s}  {'judge':>10s}  {'delta':>8s}")
    print(f"  {'-'*30} {'-'*10}  {'-'*10}  {'-'*8}")
    for case_id, j in judged_all.items():
        p = (results[case_id][args.mode].get("scoring", {}).get("total_score", 0)) * 100
        ju = j["total_score"] * 100
        delta = ju - p
        sign = "+" if delta >= 0 else ""
        print(f"  {case_id:30s} {p:>9.1f}%  {ju:>9.1f}%  {sign}{delta:>6.1f}")
    if pattern_scores and judge_scores:
        avg_p = 100 * sum(pattern_scores) / len(pattern_scores)
        avg_j = 100 * sum(judge_scores) / len(judge_scores)
        print(f"  {'AVERAGE':30s} {avg_p:>9.1f}%  {avg_j:>9.1f}%  {avg_j-avg_p:+6.1f}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(judged_all, f, indent=2, default=str)
        print(f"\nJudged scores written to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
