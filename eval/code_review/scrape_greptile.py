"""Scrape Greptile's public AI Code Review benchmark dataset.

The benchmark lives in 25 GitHub repos under the org
``ai-code-review-evaluation``. Each repo is named ``{target}-{tool}`` where
``target`` is one of ``sentry`` / ``cal.com`` / ``grafana`` / ``keycloak`` /
``discourse`` and ``tool`` is one of ``greptile`` / ``cursor`` / ``copilot``
/ ``coderabbit`` / ``graphite``.

For each (target, tool) pair the fork has 10 OPEN pull requests. The same
10 PRs (= same planted bugs) appear in every tool fork of the same target
— the only thing that varies between forks is which AI tool reviewed them.
That means we only need to scrape ONE fork per target to get all 50 cases.
We pick the ``-greptile`` fork as canonical because it's the one whose
reviews include the **human evaluator**'s ground-truth comments (search
for ``lingxiao001``-style comments in the review threads).

What we extract per PR:
  * ``title``      — PR title (the "feature" cover story for the bug)
  * ``body``       — PR description
  * ``head_sha``   — commit at the tip of the bug branch
  * ``base_sha``   — commit before the bug was introduced
  * ``head_ref``   — bug branch name
  * ``base_ref``   — main/master branch name
  * ``files``      — list of files changed (path + +/- counts)
  * ``diff``       — full unified diff (the planted bug)
  * ``ground_truth_comments`` — review comments from the human evaluator
  * ``tool_review_comments``  — review comments from Greptile bot itself

Output: one JSON file per target repo, written to ``cases/greptile_raw/``.
Each file is a list of 10 case dicts, suitable for downstream conversion
to ``cases.yaml`` by the importer step.

Usage::

    cd backend
    python ../eval/code_review/scrape_greptile.py
    python ../eval/code_review/scrape_greptile.py --target sentry
    python ../eval/code_review/scrape_greptile.py --no-diff   # skip diffs

Anonymous GitHub API rate limit is 60 requests/hour. Per target we make
roughly: 1 list call + 10 PR meta calls + 10 review-comment calls + 10
diff calls = 31 calls. Five targets × 31 calls = 155 calls, which exceeds
the anonymous limit. Workarounds:
  * Set ``GITHUB_TOKEN`` (or ``GH_TOKEN``) for 5000/hour authenticated.
  * Or run with ``--target`` to do one repo per hour.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger("scrape_greptile")
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORG = "ai-code-review-evaluation"

# The 5 target repos in the benchmark + the upstream they were forked from.
# We always scrape the ``-greptile`` fork because the human evaluator's
# ground-truth comments are most reliably present there.
TARGETS: Dict[str, Dict[str, str]] = {
    "sentry":    {"upstream": "getsentry/sentry",   "language": "python"},
    "cal.com":   {"upstream": "calcom/cal.com",     "language": "typescript"},
    "grafana":   {"upstream": "grafana/grafana",    "language": "go"},
    "keycloak":  {"upstream": "keycloak/keycloak",  "language": "java"},
    "discourse": {"upstream": "discourse/discourse", "language": "ruby"},
}

GH_API = "https://api.github.com"
USER_AGENT = "conductor-eval-scraper/1.0"
RAW_OUT_DIR = Path(__file__).resolve().parent / "cases" / "greptile_raw"


# ---------------------------------------------------------------------------
# GitHub API helper — anonymous or token-authenticated
# ---------------------------------------------------------------------------

def _gh_token() -> Optional[str]:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _gh_get(path: str, accept: str = "application/vnd.github+json") -> Any:
    """GET ``path`` from the GitHub API with retries on rate-limit / 5xx.

    Raises ``HTTPError`` on terminal failure. Returns parsed JSON for the
    default Accept header, or raw bytes when ``accept`` is set to a non-
    JSON content type (e.g. ``application/vnd.github.v3.diff``).
    """
    url = f"{GH_API}{path}" if path.startswith("/") else path
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _gh_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    delay = 1.0
    for attempt in range(5):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                body = resp.read()
                # Surface rate-limit headers for visibility
                remaining = resp.headers.get("X-RateLimit-Remaining")
                if remaining and int(remaining) < 10:
                    logger.warning(
                        "GitHub rate limit running low: remaining=%s reset=%s",
                        remaining, resp.headers.get("X-RateLimit-Reset"),
                    )
                if accept == "application/vnd.github+json":
                    return json.loads(body)
                return body.decode("utf-8", errors="replace")
        except HTTPError as exc:
            # Rate limit (403/429) or server error — back off and retry
            if exc.code in (403, 429, 500, 502, 503, 504):
                wait = delay
                # Honour Retry-After if provided
                retry_after = exc.headers.get("Retry-After") if hasattr(exc, "headers") else None
                if retry_after and retry_after.isdigit():
                    wait = max(wait, float(retry_after))
                logger.warning(
                    "HTTP %d on %s — retrying in %.1fs (attempt %d/5)",
                    exc.code, url, wait, attempt + 1,
                )
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            # 404 / 401 etc — terminal
            raise
    raise RuntimeError(f"GitHub API call failed after retries: {url}")


# ---------------------------------------------------------------------------
# Per-PR extraction
# ---------------------------------------------------------------------------

@dataclass
class PRRecord:
    """One PR worth of data, ready to feed the importer."""

    target: str          # "sentry", "cal.com", ...
    upstream: str        # "getsentry/sentry"
    language: str        # "python"
    fork_repo: str       # "ai-code-review-evaluation/sentry-greptile"
    pr_number: int
    title: str
    body: str
    head_sha: str
    base_sha: str
    head_ref: str
    base_ref: str
    additions: int
    deletions: int
    changed_files: int
    files: List[Dict[str, Any]] = field(default_factory=list)
    diff: str = ""
    ground_truth_comments: List[Dict[str, Any]] = field(default_factory=list)
    tool_review_comments: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "upstream": self.upstream,
            "language": self.language,
            "fork_repo": self.fork_repo,
            "pr_number": self.pr_number,
            "title": self.title,
            "body": self.body,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "head_ref": self.head_ref,
            "base_ref": self.base_ref,
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_files": self.changed_files,
            "files": self.files,
            "diff": self.diff,
            "ground_truth_comments": self.ground_truth_comments,
            "tool_review_comments": self.tool_review_comments,
        }


# Account names that we treat as ground-truth (human evaluator) reviews
# vs tool-bot reviews. Update as we discover more evaluator handles.
_GROUND_TRUTH_AUTHORS = {"lingxiao001", "everettbu"}
_TOOL_BOT_AUTHORS = {"greptileai", "github-actions[bot]", "greptile-apps[bot]"}


def _classify_comment(login: str) -> str:
    if login in _GROUND_TRUTH_AUTHORS:
        return "ground_truth"
    if login in _TOOL_BOT_AUTHORS:
        return "tool_bot"
    if "[bot]" in login or login.endswith("-bot"):
        return "tool_bot"
    return "other"


def _fetch_pr_record(
    target: str,
    upstream: str,
    language: str,
    fork_repo: str,
    pr_number: int,
    fetch_diff: bool,
) -> PRRecord:
    """Fetch one PR's full record from the GitHub API."""
    pr_path = f"/repos/{fork_repo}/pulls/{pr_number}"
    pr = _gh_get(pr_path)

    rec = PRRecord(
        target=target,
        upstream=upstream,
        language=language,
        fork_repo=fork_repo,
        pr_number=pr_number,
        title=pr.get("title", ""),
        body=pr.get("body") or "",
        head_sha=pr.get("head", {}).get("sha", ""),
        base_sha=pr.get("base", {}).get("sha", ""),
        head_ref=pr.get("head", {}).get("ref", ""),
        base_ref=pr.get("base", {}).get("ref", ""),
        additions=pr.get("additions", 0),
        deletions=pr.get("deletions", 0),
        changed_files=pr.get("changed_files", 0),
    )

    # Files changed (paths + per-file +/-)
    files = _gh_get(f"{pr_path}/files?per_page=100")
    rec.files = [
        {
            "path": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
        }
        for f in files
    ]

    # Inline review comments (the per-line ones — most useful for ground truth)
    inline = _gh_get(f"{pr_path}/comments?per_page=100")
    for c in inline:
        login = c.get("user", {}).get("login", "")
        kind = _classify_comment(login)
        item = {
            "kind": "inline",
            "author": login,
            "path": c.get("path", ""),
            "line": c.get("line") or c.get("original_line"),
            "body": c.get("body", ""),
        }
        if kind == "ground_truth":
            rec.ground_truth_comments.append(item)
        elif kind == "tool_bot":
            rec.tool_review_comments.append(item)

    # Top-level reviews (state + body — might contain summary verdicts)
    reviews = _gh_get(f"{pr_path}/reviews?per_page=100")
    for r in reviews:
        login = r.get("user", {}).get("login", "")
        kind = _classify_comment(login)
        body = r.get("body") or ""
        if not body.strip():
            continue
        item = {
            "kind": "review",
            "author": login,
            "state": r.get("state", ""),
            "body": body,
        }
        if kind == "ground_truth":
            rec.ground_truth_comments.append(item)
        elif kind == "tool_bot":
            rec.tool_review_comments.append(item)

    # Issue comments — these are the general PR comments (not inline). The
    # human evaluator's "PR Reviewer Guide" comment lives here, not in the
    # /comments endpoint which only returns inline review threads.
    issue_comments = _gh_get(
        f"/repos/{fork_repo}/issues/{pr_number}/comments?per_page=100"
    )
    for c in issue_comments:
        login = c.get("user", {}).get("login", "")
        kind = _classify_comment(login)
        body = c.get("body") or ""
        if not body.strip():
            continue
        item = {
            "kind": "issue_comment",
            "author": login,
            "body": body,
        }
        if kind == "ground_truth":
            rec.ground_truth_comments.append(item)
        elif kind == "tool_bot":
            rec.tool_review_comments.append(item)
        else:
            # Unknown author — could be the human evaluator with a name we
            # haven't whitelisted yet. Park as ground_truth so we can
            # inspect later, but tag the kind so the importer can filter.
            item["uncertain_author"] = True
            rec.ground_truth_comments.append(item)

    # Optionally pull the raw diff (skip with --no-diff to save API quota)
    if fetch_diff:
        rec.diff = _gh_get(pr_path, accept="application/vnd.github.v3.diff")

    return rec


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------

def _list_open_prs(fork_repo: str) -> List[int]:
    """Return the list of open PR numbers (the test cases) for one fork."""
    prs = _gh_get(f"/repos/{fork_repo}/pulls?state=open&per_page=100")
    return [pr["number"] for pr in prs]


def scrape_target(
    target: str,
    fetch_diff: bool = True,
) -> List[PRRecord]:
    """Scrape one target repo's 10 open PRs into structured records."""
    info = TARGETS[target]
    fork_repo = f"{ORG}/{target}-greptile"

    logger.info("=== %s (fork: %s) ===", target, fork_repo)
    pr_numbers = _list_open_prs(fork_repo)
    logger.info("  found %d open PRs: %s", len(pr_numbers), pr_numbers)

    records: List[PRRecord] = []
    for n in sorted(pr_numbers):
        logger.info("  fetching PR #%d ...", n)
        rec = _fetch_pr_record(
            target=target,
            upstream=info["upstream"],
            language=info["language"],
            fork_repo=fork_repo,
            pr_number=n,
            fetch_diff=fetch_diff,
        )
        logger.info(
            "    title=%r files=%d gt_comments=%d tool_comments=%d",
            rec.title[:60],
            len(rec.files),
            len(rec.ground_truth_comments),
            len(rec.tool_review_comments),
        )
        records.append(rec)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape Greptile benchmark cases into raw JSON.",
    )
    parser.add_argument(
        "--target",
        choices=sorted(TARGETS.keys()),
        help="Scrape only one target repo (default: all 5)",
    )
    parser.add_argument(
        "--no-diff",
        action="store_true",
        help="Skip the per-PR diff fetch (saves ~10 API calls per target)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=RAW_OUT_DIR,
        help=f"Output directory for raw JSON dumps (default: {RAW_OUT_DIR})",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    targets = [args.target] if args.target else list(TARGETS.keys())

    total_prs = 0
    for target in targets:
        records = scrape_target(target=target, fetch_diff=not args.no_diff)
        total_prs += len(records)
        # Slugify target name for filenames (cal.com → cal_com)
        out_path = args.out / f"{target.replace('.', '_')}.json"
        out_path.write_text(
            json.dumps([r.to_dict() for r in records], indent=2),
            encoding="utf-8",
        )
        logger.info("  wrote %s (%d PRs)", out_path, len(records))

    logger.info("Done. %d PRs scraped across %d targets.", total_prs, len(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
