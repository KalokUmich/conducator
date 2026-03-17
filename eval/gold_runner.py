"""Gold-standard baseline runner — invokes Claude Code CLI directly.

Instead of using our own AgentLoopService and 21 custom tools, this runner
calls the `claude` CLI in print mode with `--output-format stream-json`.
Claude Code uses its own tools (Read, Grep, Bash, Glob, Edit, Agent, etc.)
to freely explore the codebase and produce a PR review.

This captures the true quality ceiling: what happens when the strongest
model uses the best available tools without being constrained by our
pipeline architecture.

The full trace is saved alongside the score so we can later analyze:
  - What tools/strategies did Claude Code use that our pipeline doesn't?
  - What files did it explore that our agents missed?
  - How does its investigation depth compare to our budget-limited agents?
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from runner import CaseConfig, setup_workspace, cleanup_workspace

import sys

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.code_review.models import (
    FindingCategory,
    ReviewFinding,
    ReviewResult,
    Severity,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Claude Code prompt
# ---------------------------------------------------------------------------

_GOLD_PROMPT_TEMPLATE = """\
You are reviewing a git diff in this repository. Run `git diff HEAD~1..HEAD` to see the changes, \
then investigate the codebase thoroughly to find all bugs, security issues, \
reliability problems, and correctness defects introduced by this diff.

Use your tools freely:
- Read files around the changed code to understand context
- Grep for usages of changed functions
- Check how callers will be affected
- Look at test coverage
- Trace data flow through the codebase

Be exhaustive — trace through the code to prove each finding with evidence.

After your investigation, output your findings as a JSON array inside a \
```json code block. Each finding must have these fields:

- "title": concise description of the issue
- "category": one of "correctness", "concurrency", "security", "reliability", \
"performance", "test_coverage", "style", "maintainability"
- "severity": one of "critical", "warning", "nit"
- "confidence": float 0.0 to 1.0
- "file": file path where the issue is
- "start_line": starting line number
- "end_line": ending line number
- "evidence": array of strings citing specific code lines as evidence
- "risk": what could go wrong in production
- "suggested_fix": concrete, implementable fix

Focus on real, provable defects — not style nits or speculative concerns.
"""


# ---------------------------------------------------------------------------
# Trace data structures
# ---------------------------------------------------------------------------

@dataclass
class ToolUse:
    """A single tool call made by Claude Code during the review."""
    tool_name: str
    params: dict = field(default_factory=dict)
    result_summary: str = ""      # truncated output
    duration_ms: float = 0.0


@dataclass
class GoldTrace:
    """Full trace of a Claude Code gold-standard review.

    This captures the investigation process, not just the final output.
    Useful for analyzing what strategies Claude Code uses vs our pipeline.
    """
    tool_uses: List[ToolUse] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    grep_patterns: List[str] = field(default_factory=list)
    bash_commands: List[str] = field(default_factory=list)
    total_tool_calls: int = 0
    raw_messages: List[dict] = field(default_factory=list)
    final_text: str = ""
    model: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    session_id: str = ""

    def to_dict(self) -> dict:
        return {
            "tool_uses": [
                {"tool": t.tool_name, "params": t.params,
                 "result_summary": t.result_summary[:500]}
                for t in self.tool_uses
            ],
            "files_read": self.files_read,
            "grep_patterns": self.grep_patterns,
            "bash_commands": self.bash_commands,
            "total_tool_calls": self.total_tool_calls,
            "final_text": self.final_text,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
        }


@dataclass
class GoldRunResult:
    """Result from running a single gold-standard eval case."""
    case_id: str
    review_result: Optional[ReviewResult] = None
    trace: Optional[GoldTrace] = None
    duration_seconds: float = 0.0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Claude Code invocation
# ---------------------------------------------------------------------------

def _invoke_claude_code(
    workspace_path: str,
    prompt: str,
    model: str = "opus",
    max_budget_usd: float = 5.0,
) -> dict:
    """Invoke `claude` CLI in print mode and return the JSON result.

    Args:
        workspace_path: Directory to run in (the git workspace).
        prompt: The review prompt.
        model: Model alias (e.g. "opus", "sonnet").
        max_budget_usd: Max dollar spend per case.

    Returns:
        Parsed JSON dict from claude's --output-format json output.

    Raises:
        RuntimeError: If claude CLI fails or returns invalid JSON.
    """
    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--dangerously-skip-permissions",
        "--max-budget-usd", str(max_budget_usd),
    ]

    logger.info("Invoking claude CLI: model=%s, workspace=%s", model, workspace_path)

    # Strip ANTHROPIC_API_KEY from the environment so Claude Code uses the
    # user's subscription (Pro/Max) instead of burning API credits.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    result = subprocess.run(
        cmd,
        cwd=workspace_path,
        capture_output=True,
        text=True,
        timeout=600,  # 10 minutes max per case
        env=env,
    )

    if result.returncode != 0 and not result.stdout.strip():
        raise RuntimeError(
            f"claude CLI failed (rc={result.returncode}): {result.stderr[:500]}"
        )

    # stream-json outputs one JSON object per line (NDJSON)
    # The last message with type "result" contains the final output
    messages = []
    result_msg = None
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            messages.append(msg)
            if msg.get("type") == "result":
                result_msg = msg
        except json.JSONDecodeError:
            continue

    if not result_msg:
        raise RuntimeError(
            f"No result message in claude output. "
            f"Lines: {len(messages)}, stderr: {result.stderr[:300]}"
        )

    # Attach all messages for trace extraction
    result_msg["_all_messages"] = messages
    return result_msg


# ---------------------------------------------------------------------------
# Trace extraction
# ---------------------------------------------------------------------------

def _extract_trace(result_msg: dict) -> GoldTrace:
    """Extract a structured trace from claude CLI stream-json output.

    Parses the NDJSON messages to find tool calls, files read, etc.
    """
    trace = GoldTrace()
    messages = result_msg.get("_all_messages", [])

    trace.final_text = result_msg.get("result", "")
    trace.cost_usd = result_msg.get("total_cost_usd", 0.0)
    trace.session_id = result_msg.get("session_id", "")
    trace.duration_ms = result_msg.get("duration_ms", 0.0)
    trace.model = result_msg.get("model", "")
    trace.raw_messages = messages

    usage = result_msg.get("usage", {})
    trace.input_tokens = usage.get("input_tokens", 0)
    trace.output_tokens = usage.get("output_tokens", 0)

    files_read = set()
    grep_patterns = []
    bash_commands = []

    for msg in messages:
        msg_type = msg.get("type", "")

        # assistant messages may contain tool_use blocks
        if msg_type == "assistant":
            for block in msg.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    params = block.get("input", {})
                    tool_use = ToolUse(tool_name=tool_name, params=params)
                    trace.tool_uses.append(tool_use)
                    trace.total_tool_calls += 1

                    # Extract specific info based on tool type
                    if tool_name == "Read":
                        fp = params.get("file_path", "")
                        if fp:
                            files_read.add(fp)
                    elif tool_name == "Grep":
                        pat = params.get("pattern", "")
                        if pat:
                            grep_patterns.append(pat)
                    elif tool_name == "Bash":
                        cmd = params.get("command", "")
                        if cmd:
                            bash_commands.append(cmd)
                    elif tool_name == "Glob":
                        pat = params.get("pattern", "")
                        if pat:
                            grep_patterns.append(f"[glob] {pat}")

        # tool_result messages contain tool outputs
        elif msg_type == "tool_result":
            # Try to attach result summary to the last tool use
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if trace.tool_uses:
                trace.tool_uses[-1].result_summary = str(content)[:500]

    trace.files_read = sorted(files_read)
    trace.grep_patterns = grep_patterns
    trace.bash_commands = bash_commands

    return trace


# ---------------------------------------------------------------------------
# Finding parsing
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```")
_JSON_BARE_RE = re.compile(r"\[[\s\S]*\]")
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")

_SEVERITY_MAP = {
    "critical": Severity.CRITICAL,
    "warning": Severity.WARNING,
    "nit": Severity.NIT,
    "praise": Severity.PRAISE,
}

_CATEGORY_MAP = {
    "correctness": FindingCategory.CORRECTNESS,
    "concurrency": FindingCategory.CONCURRENCY,
    "security": FindingCategory.SECURITY,
    "reliability": FindingCategory.RELIABILITY,
    "performance": FindingCategory.PERFORMANCE,
    "test_coverage": FindingCategory.TEST_COVERAGE,
    "style": FindingCategory.STYLE,
    "maintainability": FindingCategory.MAINTAINABILITY,
}


def _raw_to_finding(raw: dict) -> Optional[ReviewFinding]:
    """Convert a raw dict to a ReviewFinding. Returns None if invalid."""
    if not isinstance(raw, dict):
        return None
    if not raw.get("title") and not raw.get("file"):
        return None

    severity_str = str(raw.get("severity", "warning")).lower()
    category_str = str(raw.get("category", "correctness")).lower()

    try:
        return ReviewFinding(
            title=raw.get("title", "Untitled finding"),
            category=_CATEGORY_MAP.get(category_str, FindingCategory.CORRECTNESS),
            severity=_SEVERITY_MAP.get(severity_str, Severity.WARNING),
            confidence=float(raw.get("confidence", 0.7)),
            file=raw.get("file", ""),
            start_line=int(raw.get("start_line", 0)),
            end_line=int(raw.get("end_line", 0)),
            evidence=raw.get("evidence", []),
            risk=raw.get("risk", ""),
            suggested_fix=raw.get("suggested_fix", ""),
            agent="gold-claude-code",
            reasoning=raw.get("reasoning", ""),
        )
    except (TypeError, ValueError):
        return None


def _try_parse_json_array(text: str) -> Optional[list]:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def parse_gold_findings(answer: str) -> List[ReviewFinding]:
    """Extract findings from Claude Code's review output.

    Tries: 1) JSON in code block, 2) bare JSON array, 3) individual objects.
    """
    findings: List[ReviewFinding] = []

    # Strategy 1: JSON in markdown code block
    for m in _JSON_BLOCK_RE.finditer(answer):
        raw_list = _try_parse_json_array(m.group(1))
        if raw_list is not None:
            for raw in raw_list:
                f = _raw_to_finding(raw)
                if f:
                    findings.append(f)
            if findings:
                return findings

    # Strategy 2: Bare JSON array
    for m in _JSON_BARE_RE.finditer(answer):
        raw_list = _try_parse_json_array(m.group())
        if raw_list is not None:
            for raw in raw_list:
                f = _raw_to_finding(raw)
                if f:
                    findings.append(f)
            if findings:
                return findings

    # Strategy 3: Individual JSON objects
    for m in _JSON_OBJECT_RE.finditer(answer):
        try:
            raw = json.loads(m.group())
            f = _raw_to_finding(raw)
            if f:
                findings.append(f)
        except (json.JSONDecodeError, ValueError):
            continue

    if not findings:
        logger.warning("Failed to parse any findings from Claude Code output")

    return findings


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_gold_case(
    case: CaseConfig,
    source_dir: str,
    patch_dir: str,
    model: str = "opus",
    max_budget_usd: float = 5.0,
) -> GoldRunResult:
    """Run a single case with Claude Code CLI (gold-standard).

    Sets up the workspace, invokes `claude` CLI with the review prompt,
    and captures the full trace + findings.

    Args:
        case: Case configuration.
        source_dir: Path to the plain source directory.
        patch_dir: Directory containing patch files.
        model: Claude Code model alias (e.g. "opus", "sonnet").
        max_budget_usd: Max dollar spend for this case.

    Returns:
        GoldRunResult with findings, trace, and metadata.
    """
    patch_path = os.path.join(patch_dir, case.patch)
    if not os.path.exists(patch_path):
        return GoldRunResult(case_id=case.id, error=f"Patch not found: {patch_path}")

    workspace = None
    start_time = time.monotonic()
    try:
        workspace = setup_workspace(source_dir, patch_path)

        # Invoke Claude Code
        result_msg = _invoke_claude_code(
            workspace_path=workspace,
            prompt=_GOLD_PROMPT_TEMPLATE,
            model=model,
            max_budget_usd=max_budget_usd,
        )

        # Check for errors
        if result_msg.get("is_error"):
            error_text = result_msg.get("result", "Unknown error")
            return GoldRunResult(
                case_id=case.id,
                error=f"Claude Code error: {error_text}",
                duration_seconds=time.monotonic() - start_time,
            )

        # Extract trace
        trace = _extract_trace(result_msg)

        # Parse findings from the final text
        findings = parse_gold_findings(trace.final_text)

        # Files reviewed = files Claude Code read via Read tool
        files_reviewed = trace.files_read

        review_result = ReviewResult(
            diff_spec="HEAD~1..HEAD",
            findings=findings,
            files_reviewed=files_reviewed,
            total_tokens=trace.input_tokens + trace.output_tokens,
            total_iterations=trace.total_tool_calls,
            total_duration_ms=trace.duration_ms,
            synthesis=trace.final_text,
        )

        duration = time.monotonic() - start_time
        return GoldRunResult(
            case_id=case.id,
            review_result=review_result,
            trace=trace,
            duration_seconds=duration,
        )

    except Exception as e:
        duration = time.monotonic() - start_time
        return GoldRunResult(
            case_id=case.id,
            error=str(e),
            duration_seconds=duration,
        )
    finally:
        if workspace:
            cleanup_workspace(workspace)
