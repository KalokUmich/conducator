"""LLM-as-Judge for qualitative evaluation of code review output.

Hard-graded rubric (4 dimensions, anchored 1/3/5 for the binary-ish ones):

  completeness          — did the reviewer find the planted bug? (1, 3, 5)
  reasoning_quality     — is the evidence chain complete and verifiable? (1-5)
  actionability         — is the suggested fix concrete? (1, 3, 5)
  false_positive_quality — discipline on secondary findings (1-5, penalty for noise)

The judge is given the **deterministic match data** from ``scorer.score_case``
as a starting point — that means it doesn't have to re-do title/file/line
matching, and can focus on QUALITY (was the evidence real, was the fix
concrete, were the secondary findings legitimate).

Field names are preserved for backward compatibility with ``report.py`` and
existing baseline JSON files; the rubric meaning has tightened.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.ai_provider.base import AIProvider  # noqa: E402

# scorer is in the same directory; tolerate both layouts (when invoked from
# eval/code_review/ vs from another cwd via sys.path tricks).
try:
    from scorer import CaseScore, FindingMatch  # type: ignore
except ImportError:  # pragma: no cover — fallback for unusual sys.path
    _EVAL_DIR = str(Path(__file__).resolve().parent)
    if _EVAL_DIR not in sys.path:
        sys.path.insert(0, _EVAL_DIR)
    from scorer import CaseScore, FindingMatch  # type: ignore


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """\
You are a STRICT code review quality judge. You evaluate how well an automated \
reviewer caught a bug planted in open-source code. You hold reviewers to a \
high bar — when in doubt, score lower, not higher. "Trying hard" earns no \
credit; only outcomes do.

You will receive, for one case:
1. A description of the planted bug (ground truth).
2. The full list of expected findings — not just the primary one.
3. A **deterministic match table** produced by the line/file/title scorer.
   This tells you which actual findings matched which expected findings on
   which dimensions. Trust this table for the mechanical matching — your
   job is the QUALITATIVE judgment ON TOP of it (was the evidence real?
   was the fix concrete? was secondary noise excessive?).
4. The reviewer's full findings list (with evidence and suggested fix).
5. (Optional) the reviewer's synthesis prose. The findings list is the
   actual deliverable — synthesis is commentary. Do NOT credit synthesis
   prose if it contradicts or exaggerates the findings list.

# Rubric

You will score four criteria. Most use 3-anchor scales (1, 3, 5) so you can
NOT hide indecision in a 2 or 4. The 5-anchor scales (reasoning_quality and
false_positive_quality) require explicit justification for any 4 or 5.

## completeness (1, 3, or 5) — did they find the planted bug?

- **1 (MISSED)**  — No actual finding mapped to the primary expected finding,
  OR the reviewer mentioned the bug only in passing in synthesis without a
  structured finding. A finding pointing at the wrong file is also a 1.
- **3 (PARTIAL)** — A finding maps to the expected one (per the match table)
  but with at least one of: wrong root cause framing, wrong category, wrong
  severity by more than one level, or missing the key trigger condition in
  the evidence. The bug was noticed but not fully understood.
- **5 (FOUND)**   — A finding cleanly matches the expected primary bug:
  correct file, correct line range, correct root cause framing, severity
  within one level of expected. If the case has multiple expected findings,
  ALL primary ones must be matched for a 5; otherwise cap at 3.

You MAY NOT score 2 or 4 on completeness. Pick 1, 3, or 5.

## reasoning_quality (1-5) — is the evidence verifiable?

- **1** No evidence cited, or evidence is wrong / fabricated / quotes code
  that does not exist at the cited location.
- **2** Generic claims ("this could fail") with no concrete code reference.
- **3** Cites the right file but evidence is shallow (one-liner, no chain).
  Could not be verified by an independent reader without re-reading the code.
- **4** Cites code lines with a clear chain of reasoning, but missing one
  step — e.g. doesn't show why the trigger is reachable, or doesn't explain
  what input pattern causes the bug.
- **5** Full evidence chain: cites the exact line, identifies the trigger
  condition, and explains why the trigger is reachable from a realistic call
  site. An independent reader could re-derive the bug from the evidence alone.

Score 4 or 5 ONLY if the evidence would convince a skeptical senior engineer
without them having to re-read the source. If you find yourself thinking "I'd
need to verify this", that is a 3 at most.

## actionability (1, 3, or 5) — is the fix concrete?

- **1 (NO FIX)** No fix suggested, or the fix is "investigate this" / "fix
  the bug" non-fix.
- **3 (NAMED)**  Fix is named in the right direction ("add a null check",
  "use parameterized queries", "restore the timeout") but does not show
  WHERE to apply it or HOW. The reader still has to design the fix.
- **5 (CONCRETE)** Copy-pasteable patch at the correct location: shows the
  exact line/method to change and the replacement code or configuration.
  A junior engineer could apply it without further investigation.

You MAY NOT score 2 or 4 on actionability. Pick 1, 3, or 5.

## false_positive_quality (1-5) — discipline on secondary findings

This applies ONLY to findings that did NOT match an expected finding (the
"extras" — see the match table). The planted bug itself is covered by
``completeness`` and is excluded here, even if mislabeled.

- **1** 4+ irrelevant secondary findings, OR any finding that the
  ``DO NOT FLAG`` list of the project explicitly excludes (style, naming,
  pre-existing issues, generated/vendored code, premature abstractions,
  speculative concern with no concrete trigger).
- **2** 2-3 irrelevant secondaries.
- **3** 1 irrelevant secondary, or all secondaries are mild "speculative
  concern" without DO-NOT-FLAG violations.
- **4** All secondary findings are defensible (not the planted bug, but
  plausibly real defects that a senior engineer would not dismiss).
- **5** No secondary findings at all, OR all secondaries are clearly real
  bugs that the case ground truth simply doesn't list. Also score 5 if there
  are no extras to evaluate.

Note: a reviewer that emits ZERO findings does NOT automatically get a 5
here — that case scores 1 because the reviewer also failed completeness.
Score false_positive_quality only on the SECONDARY findings, treating the
no-findings case as a 5 ONLY when completeness was also high.

# Anti-bias rules

- DO NOT inflate scores out of charity. "Almost found it" is a 3, not a 4.
- DO NOT credit synthesis prose if the findings list disagrees.
- DO NOT credit a finding for the right bug if it points at the wrong file.
- If the reviewer flagged the bug under the WRONG category (e.g. reported
  a security bug as "reliability"), cap completeness at 3.
- If you cannot decide between two scores, pick the LOWER one and explain why.

# Output format

You MUST respond with the following two blocks, in this exact order, with
no other text outside them:

<analysis>
For each criterion in turn, write 1-3 sentences of justification. State which
actual finding(s) you mapped to which expected finding(s). Be specific —
quote evidence text or fix text when relevant. Vague verdicts ("good
analysis", "well-reasoned") are not acceptable and will be considered an
error.
</analysis>

<verdict>
completeness: <1|3|5>
reasoning_quality: <1|2|3|4|5>
actionability: <1|3|5>
false_positive_quality: <1|2|3|4|5>
</verdict>"""


# Few-shot examples — anchor the LLM to concrete grading. Two examples cover
# a CLEAN PASS and a BORDERLINE FAIL so the model sees both ends of the bar.
JUDGE_FEW_SHOT_EXAMPLES = """\
# Worked example 1 — clean pass (high scores justified)

Suppose the planted bug is "Missing timeout parameter in HTTPAdapter.send()
at adapters.py:471-483 — connections can hang forever". The reviewer emits
exactly one finding:

  1. **Removed timeout in HTTPAdapter.send** [critical] (reliability)
     adapters.py:471-483
     Evidence: "Line 478 sets timeout=None unconditionally, replacing the
     prior call to TimeoutSauce(connect=timeout, read=timeout). The patch
     deleted lines 472-477 (TimeoutSauce construction). Any caller using
     the default Session.get() has no read timeout, so requests against
     a slow server will block until the OS TCP keepalive (>2 hours)."
     Fix: "Restore TimeoutSauce: replace line 478 with `timeout =
     TimeoutSauce(connect=timeout, read=timeout)`."

Match table says: actual[0] -> expected[0] (title=YES, file=YES, line=YES,
severity=YES, recommendation=YES).

Verdict:
  completeness: 5  (clean match on the primary expected finding)
  reasoning_quality: 5  (cites exact line, identifies trigger, explains
                        reachability via Session.get default path)
  actionability: 5  (copy-pasteable fix at the correct line)
  false_positive_quality: 5  (no secondary findings)

# Worked example 2 — borderline fail (the trap to avoid)

Same planted bug. Reviewer emits four findings:

  1. **Network code may hang** [warning] (reliability) adapters.py:480
     Evidence: "The send method handles timeouts. There may be a hang."
     Fix: "Add proper timeout handling."

  2. **Inconsistent indentation in send()** [nit] (style) adapters.py:475
     Evidence: "4-space indent inside the try block looks off."
     Fix: "Re-indent to project standard."

  3. **TODO comment left in code** [nit] (style) adapters.py:490
     Evidence: "Line 490 has a # TODO with no owner."
     Fix: "Add owner or ticket."

  4. **Variable name `chunked` is unclear** [nit] (style) adapters.py:495
     Evidence: "Could be more descriptive."
     Fix: "Rename to `is_chunked`."

Match table says: actual[0] -> expected[0] (title=YES, file=YES, line=NO
[wrong line — bug is at 471-483, finding points at 480 only], severity=NO
[expected critical, got warning], recommendation=NO [vague]).

Verdict:
  completeness: 3  (the bug WAS noticed but with wrong line range, wrong
                   severity, and a too-vague title — bug noticed but not
                   understood)
  reasoning_quality: 2  ("there may be a hang" is a generic claim, no
                        concrete chain, no trigger explanation)
  actionability: 3  ("add proper timeout handling" names the direction
                    but doesn't show where or how)
  false_positive_quality: 1  (3 style findings on the DO NOT FLAG list —
                             indentation, TODO ownership, naming — clear
                             noise)

# Notice the discipline

In Example 2 the temptation is to give completeness=4 because "they did
notice it". The rubric forbids 4 — you MUST pick 3 or 5. Picking 3 is
correct: severity is wrong, line is wrong, title is vague. That's a partial
detection, not a full one.
"""


JUDGE_PROMPT_TEMPLATE = """\
{few_shot}

# Case under evaluation

## Planted bug (ground truth)
**Title:** {case_title}
**Description:** {case_description}

## Expected findings ({n_expected} total)
{expected_findings_text}

## Deterministic match table
The line/title/file scorer already computed which actual findings matched
which expected findings. Use this as your starting point — DO NOT re-do
this matching. Apply your QUALITATIVE judgment on top.

{match_table}

## Reviewer's findings ({n_findings} total)
{findings_text}

## Reviewer's synthesis
{synthesis}

---

Apply the rubric. Be strict. When in doubt, score lower."""


# ---------------------------------------------------------------------------
# Verdict dataclass — field names preserved for backward compat
# ---------------------------------------------------------------------------

# Per-dimension weights for the composite ``average``. detection matters
# most (40%), then evidence quality (25%), then secondary discipline (20%),
# then actionability (15%).  Numbers must sum to 1.0.
_WEIGHTS = {
    "completeness": 0.40,
    "reasoning_quality": 0.25,
    "false_positive_quality": 0.20,
    "actionability": 0.15,
}


@dataclass
class JudgeVerdict:
    """Parsed verdict from the LLM judge.

    Field names match the legacy schema so ``report.py`` and existing
    baseline JSON files keep working — but the meaning of each field is
    now governed by the strict rubric in ``JUDGE_SYSTEM_PROMPT``.
    """

    completeness: int = 0          # 1, 3, or 5
    reasoning_quality: int = 0     # 1-5
    actionability: int = 0         # 1, 3, or 5
    false_positive_quality: int = 0  # 1-5
    analysis: str = ""
    raw_response: str = ""
    error: Optional[str] = None

    @property
    def average(self) -> float:
        """Weighted average across the four dimensions.

        completeness gets 40%, reasoning_quality 25%, false_positive_quality
        20%, actionability 15%. Missing scores (still 0 because parsing
        failed) are excluded and the remaining weights are renormalised.
        """
        weighted: List[tuple] = []
        for field_name, weight in _WEIGHTS.items():
            score = getattr(self, field_name)
            if score > 0:
                weighted.append((score, weight))
        if not weighted:
            return 0.0
        total_w = sum(w for _, w in weighted)
        return sum(s * w for s, w in weighted) / total_w

    def to_dict(self) -> dict:
        return {
            "completeness": self.completeness,
            "reasoning_quality": self.reasoning_quality,
            "actionability": self.actionability,
            "false_positive_quality": self.false_positive_quality,
            "average": round(self.average, 2),
            "analysis": self.analysis,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Helpers — format expected findings, match table, and actual findings
# ---------------------------------------------------------------------------

def _format_expected_findings(expected: list) -> str:
    """Render every expected finding as a numbered Markdown block.

    Previously the judge only saw the first expected finding, so multi-bug
    cases were unscoreable on completeness. This format gives the judge
    explicit visibility into all of them.
    """
    if not expected:
        return "(No expected findings defined.)"
    lines = []
    for i, exp in enumerate(expected, 1):
        line_range = exp.get("line_range", [])
        line_str = (
            f"{line_range[0]}-{line_range[1]}"
            if len(line_range) == 2 else "?"
        )
        lines.append(
            f"**Expected #{i}:** "
            f"file={exp.get('file_pattern', '?')} "
            f"lines={line_str} "
            f"severity={exp.get('severity', '?')} "
            f"category={exp.get('category', '?')}"
        )
        rec = exp.get("recommendation")
        if rec:
            lines.append(f"  Recommended fix: {rec}")
    return "\n".join(lines)


def _format_match_table(
    matches: List[FindingMatch],
    n_expected: int,
    n_findings: int,
) -> str:
    """Render the per-(expected, actual) match results as a compact table.

    Format::

        expected[0] <- actual[2]   title=YES file=YES line=NO  severity=NO  rec=NO
        expected[1] <- (no match)
        unmatched actual: [0, 1, 3]  (these are the "extras" — secondary findings)
    """
    if not matches and n_expected == 0:
        return "(No matches; no expected findings.)"

    matched_actual = {m.actual_index for m in matches}
    matched_expected = {m.expected_index for m in matches}

    lines = []
    for exp_idx in range(n_expected):
        match = next((m for m in matches if m.expected_index == exp_idx), None)
        if match is None:
            lines.append(f"  expected[{exp_idx}] <- (no match — MISSED)")
            continue
        lines.append(
            f"  expected[{exp_idx}] <- actual[{match.actual_index}]   "
            f"title={'YES' if match.title_match else 'no '} "
            f"file={'YES' if match.file_match else 'no '} "
            f"line={'YES' if match.line_match else 'no '} "
            f"severity={'YES ' if match.severity_match >= 0.99 else 'ADJ ' if match.severity_match >= 0.49 else 'no  '}"
            f"category={'YES' if match.category_match else 'no '} "
            f"rec={'YES' if match.recommendation_match else 'no '}"
        )

    extras = sorted(set(range(n_findings)) - matched_actual)
    if extras:
        lines.append(
            f"\n  unmatched actual (secondary findings): "
            f"{extras}  ← evaluate these for false_positive_quality"
        )
    else:
        lines.append("\n  unmatched actual: (none)")

    if not matched_expected and n_expected > 0:
        lines.append("\n  WARNING: NO expected finding was matched. Completeness should be 1.")

    return "\n".join(lines)


def _format_findings(findings: list) -> str:
    """Render the reviewer's actual findings with evidence + fix excerpts."""
    if not findings:
        return "(No findings reported.)"
    blocks = []
    for i, f in enumerate(findings):
        evidence_str = "; ".join(f.evidence[:3]) if f.evidence else "(none)"
        fix_str = (f.suggested_fix or "(none)")[:300]
        blocks.append(
            f"**Finding [{i}]:** {f.title}\n"
            f"  severity={f.severity.value} category={f.category.value} "
            f"file={f.file}:{f.start_line}-{f.end_line} agent={f.agent}\n"
            f"  evidence: {evidence_str}\n"
            f"  fix: {fix_str}"
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def judge_case(
    provider: AIProvider,
    case_title: str,
    case_description: str,
    expected_findings: list,
    findings: list,
    synthesis: str,
    case_score: Optional[CaseScore] = None,
    model: Optional[str] = None,
) -> JudgeVerdict:
    """Run the LLM judge on a single case.

    Args:
        provider: AI provider with ``call_model``.
        case_title: Human-readable case name.
        case_description: What the planted bug is.
        expected_findings: Ground-truth list of expected finding dicts.
        findings: Actual ``ReviewFinding`` objects from the review.
        synthesis: Reviewer's synthesis prose.
        case_score: Optional ``CaseScore`` from ``scorer.score_case``. When
            provided, its ``matches`` list is rendered into a deterministic
            match table that the judge uses as the matching baseline. Pass
            this in whenever possible — it sharply improves grading
            consistency.
        model: Optional model override.

    Returns:
        A populated ``JudgeVerdict``. On parser failure, the ``error``
        field is set and per-dimension scores remain 0.
    """
    expected_text = _format_expected_findings(expected_findings)
    findings_text = _format_findings(findings)

    matches = case_score.matches if case_score else []
    match_table = _format_match_table(
        matches=matches,
        n_expected=len(expected_findings),
        n_findings=len(findings),
    )

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        few_shot=JUDGE_FEW_SHOT_EXAMPLES,
        case_title=case_title,
        case_description=case_description,
        n_expected=len(expected_findings),
        expected_findings_text=expected_text,
        match_table=match_table,
        n_findings=len(findings),
        findings_text=findings_text,
        synthesis=synthesis or "(No synthesis provided.)",
    )

    try:
        response = provider.call_model(
            prompt=prompt,
            max_tokens=2500,
            system=JUDGE_SYSTEM_PROMPT,
        )
        return _parse_verdict(response)
    except Exception as exc:
        return JudgeVerdict(error=str(exc), raw_response="")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _parse_verdict(response: str) -> JudgeVerdict:
    """Parse the structured ``<analysis>`` + ``<verdict>`` blocks.

    Enforces the discrete-anchor rules: ``completeness`` and ``actionability``
    must be one of {1, 3, 5}; the other two may be 1-5. Out-of-range scores
    are rounded to the nearest valid anchor and the analysis is annotated.
    """
    verdict = JudgeVerdict(raw_response=response)

    analysis_match = re.search(
        r"<analysis>(.*?)</analysis>", response, re.DOTALL
    )
    if analysis_match:
        verdict.analysis = analysis_match.group(1).strip()

    verdict_match = re.search(
        r"<verdict>(.*?)</verdict>", response, re.DOTALL
    )
    if not verdict_match:
        verdict.error = "Could not parse <verdict> block from response"
        return verdict

    verdict_text = verdict_match.group(1)

    discrete_135 = {"completeness", "actionability"}
    full_15 = {"reasoning_quality", "false_positive_quality"}

    for field_name in discrete_135 | full_15:
        pattern = rf"{field_name}\s*[:=]\s*(\d)"
        m = re.search(pattern, verdict_text)
        if not m:
            continue
        score = int(m.group(1))
        if field_name in discrete_135:
            # Snap to nearest valid anchor in {1, 3, 5}.
            score = min({1, 3, 5}, key=lambda v: abs(v - score))
        else:
            score = max(1, min(5, score))
        setattr(verdict, field_name, score)

    return verdict
