"""Clone the 5 Greptile-benchmark fork repos and pre-extract per-case base snapshots.

After ``scrape_greptile.py`` + ``import_greptile.py`` populate the cases.yaml
files, each case carries:
  * ``base_ref``  — the upstream-style base branch this PR was opened against
  * ``source_dir`` — the per-case source directory we WILL create here

This script:
  1. Clones each ``ai-code-review-evaluation/{target}-greptile`` fork once
     (full git history, since we need branch checkouts).
  2. For every case, runs ``git archive base_ref`` against the cloned fork
     and extracts the result to ``repos/greptile_bases/{target}/{pr_num}/``.
  3. Verifies each extracted snapshot is non-empty.

The result: ``repos/greptile_bases/sentry/001/`` is a clean snapshot of the
``performance-optimization-baseline`` branch at the SHA the PR was opened
against, ready for ``runner.setup_workspace`` to copy + ``git apply`` the
matching patch on top.

Usage::

    cd backend
    python ../eval/code_review/materialize_greptile_bases.py
    python ../eval/code_review/materialize_greptile_bases.py --target sentry
    python ../eval/code_review/materialize_greptile_bases.py --skip-clone

Idempotent: safely re-runnable. Existing clones are reused (``git fetch``
to refresh), existing base snapshots are skipped unless ``--force``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger("materialize_greptile_bases")
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.INFO)


_THIS_DIR = Path(__file__).resolve().parent
_REPOS_DIR = _THIS_DIR / "repos"
_BASES_ROOT = _REPOS_DIR / "greptile_bases"
_CASES_DIR = _THIS_DIR / "cases"


def _regenerate_patch(
    fork_dir: Path,
    base_sha: str,
    head_sha: str,
    out_path: Path,
) -> bool:
    """Regenerate the case patch from a local ``git diff base_sha head_sha``.

    The GitHub API's ``.diff`` endpoint is computed against the **merge base**
    (so that user-only changes are shown when ``base`` has moved). When we
    materialize the snapshot at the literal ``base_sha`` rather than the merge
    base, the API-returned diff stops applying cleanly because of unrelated
    files added on the base branch in between.

    Computing the diff locally with ``git diff base_sha head_sha`` produces a
    patch that diffs THOSE TWO commits directly — guaranteed to apply on top
    of the ``base_sha`` snapshot.
    """
    if not (base_sha and head_sha):
        return False
    proc = subprocess.run(
        ["git", "-C", str(fork_dir), "diff", base_sha, head_sha],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        logger.error(
            "    git diff failed for %s..%s in %s: %s",
            base_sha, head_sha, fork_dir, proc.stderr.decode("utf-8", errors="replace"),
        )
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(proc.stdout)
    return True

ORG = "ai-code-review-evaluation"

# (target_name, fork_repo_slug)
TARGETS: Dict[str, str] = {
    "sentry":    "sentry-greptile",
    "cal_com":   "cal.com-greptile",
    "grafana":   "grafana-greptile",
    "keycloak":  "keycloak-greptile",
    "discourse": "discourse-greptile",
}


def _run(cmd: List[str], cwd: Optional[Path] = None, capture: bool = False) -> str:
    """Run a subprocess and return stdout (or empty string)."""
    logger.debug("$ %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=capture,
        text=True,
    )
    return result.stdout if capture else ""


def _clone_url(slug: str) -> str:
    """Build a git clone URL, using GITHUB_TOKEN if available for higher rate limits."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return f"https://{token}@github.com/{ORG}/{slug}.git"
    return f"https://github.com/{ORG}/{slug}.git"


def clone_or_update_fork(target: str, slug: str) -> Path:
    """Clone the fork if missing, otherwise ``git fetch`` it.

    Returns the local path. Anonymous clones over HTTPS are slow but reliable;
    a token in the environment is used transparently for the URL.
    """
    dest = _REPOS_DIR / slug
    if dest.exists():
        logger.info("[%s] fetch existing clone at %s", target, dest)
        _run(["git", "-C", str(dest), "fetch", "--all", "--quiet"])
        return dest
    _REPOS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("[%s] cloning %s/%s ...", target, ORG, slug)
    _run([
        "git", "clone",
        "--quiet",
        # We DO need the branches (so no --depth=1), but we don't need
        # blob history for files we won't read. Treeless filter saves a lot.
        "--filter=blob:none",
        _clone_url(slug),
        str(dest),
    ])
    return dest


def _resolve_ref(fork_dir: Path, base_ref: str) -> Optional[str]:
    """Find a usable git ref name for ``base_ref`` inside ``fork_dir``.

    After ``git clone`` only the default branch (usually ``master`` /
    ``main``) exists as a local ref; non-default branches live under
    ``refs/remotes/origin/...``. Try the candidates in order so callers
    can simply pass the upstream branch name.
    """
    candidates = [base_ref, f"origin/{base_ref}", f"refs/remotes/origin/{base_ref}"]
    for cand in candidates:
        result = subprocess.run(
            ["git", "-C", str(fork_dir), "rev-parse", "--verify", cand],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return cand
    return None


def materialize_base(
    fork_dir: Path,
    base_ref: str,
    out_dir: Path,
    force: bool,
) -> bool:
    """Extract a snapshot of ``base_ref`` from ``fork_dir`` to ``out_dir``.

    Uses ``git archive`` (no working tree mutation, no .git directory in the
    output, fast). Returns True if the snapshot was created or already existed.
    """
    if out_dir.exists() and any(out_dir.iterdir()):
        if not force:
            logger.debug("    %s already exists — skipping", out_dir)
            return True
        shutil.rmtree(out_dir)

    resolved_ref = _resolve_ref(fork_dir, base_ref)
    if resolved_ref is None:
        logger.error(
            "    cannot resolve ref %r in %s (tried local, origin/, refs/remotes/origin/)",
            base_ref, fork_dir,
        )
        return False

    out_dir.mkdir(parents=True, exist_ok=True)
    # `git archive --format=tar resolved_ref | tar -x -C out_dir`
    archive_cmd = ["git", "-C", str(fork_dir), "archive", "--format=tar", resolved_ref]
    proc = subprocess.run(
        archive_cmd,
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        logger.error(
            "    git archive failed for ref=%r in %s: %s",
            resolved_ref, fork_dir, proc.stderr.decode("utf-8", errors="replace"),
        )
        return False

    # Extract the tar from memory
    import io
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as tf:
        tf.extractall(out_dir)

    n_files = sum(1 for _ in out_dir.rglob("*") if _.is_file())
    if n_files == 0:
        logger.warning("    extracted snapshot is EMPTY for %s @ %s", fork_dir.name, base_ref)
        return False
    logger.info("    materialized %s @ %s -> %s (%d files)", fork_dir.name, base_ref, out_dir, n_files)
    return True


def process_target(target: str, slug: str, skip_clone: bool, force: bool) -> Dict[str, int]:
    """Clone the fork (if needed) and materialize all its case bases.

    Loads cases from BOTH ``cases.yaml`` (auto-imported) and
    ``manual_cases.yaml`` (hand-annotated). The same per-PR materialization
    logic applies to both — they're just different sources of metadata.
    """
    target_dir = _CASES_DIR / f"greptile_{target}"
    cases: list = []

    cases_yaml = target_dir / "cases.yaml"
    if cases_yaml.exists():
        auto = yaml.safe_load(cases_yaml.read_text(encoding="utf-8")) or {}
        cases.extend(auto.get("cases", []))

    manual_yaml = target_dir / "manual_cases.yaml"
    if manual_yaml.exists():
        manual = yaml.safe_load(manual_yaml.read_text(encoding="utf-8")) or {}
        cases.extend(manual.get("cases", []))

    if not cases:
        logger.warning("[%s] no cases at %s — skipping", target, target_dir)
        return {"materialized": 0, "skipped": 0, "errored": 0, "patches_regenerated": 0}

    logger.info("[%s] %d cases to materialize", target, len(cases))

    if skip_clone:
        fork_dir = _REPOS_DIR / slug
        if not fork_dir.exists():
            logger.error("[%s] --skip-clone but fork not present at %s", target, fork_dir)
            return {"materialized": 0, "skipped": 0, "errored": len(cases)}
    else:
        fork_dir = clone_or_update_fork(target, slug)

    stats = {"materialized": 0, "skipped": 0, "errored": 0, "patches_regenerated": 0}
    for case in cases:
        # The GitHub PR view diffs ``merge-base(base_sha, head_sha) -> head_sha``
        # so user-only changes are shown even when the base branch has moved
        # forward. We must materialize the snapshot AT THE MERGE BASE for the
        # PR's diff to apply cleanly. Using ``base_sha`` directly causes
        # binary-file conflicts whenever the base branch advanced after the
        # PR was opened.
        base_sha = case.get("base_sha", "")
        head_ref = case.get("head_ref", "")
        source_dir = case.get("source_dir", "")
        patch_rel = case.get("patch", "")
        if not (base_sha and head_ref and source_dir and patch_rel):
            logger.warning("    case %s missing required fields — skipping", case.get("id"))
            stats["skipped"] += 1
            continue

        # Resolve head_ref → head_sha so we can compute the merge base
        resolved_head = _resolve_ref(fork_dir, head_ref)
        if not resolved_head:
            logger.warning(
                "    cannot resolve head_ref=%r for %s — skipping",
                head_ref, case.get("id"),
            )
            stats["errored"] += 1
            continue

        # merge_base = the commit at which head_sha diverged from base_sha.
        # The PR's "what changed" diff is computed against this commit.
        mb = subprocess.run(
            ["git", "-C", str(fork_dir), "merge-base", base_sha, resolved_head],
            check=False,
            capture_output=True,
            text=True,
        )
        if mb.returncode != 0 or not mb.stdout.strip():
            logger.warning(
                "    merge-base(%s, %s) failed for %s: %s",
                base_sha[:10], resolved_head, case.get("id"), mb.stderr.strip(),
            )
            stats["errored"] += 1
            continue
        merge_base_sha = mb.stdout.strip()

        out_dir = _THIS_DIR / source_dir
        ok = materialize_base(fork_dir, merge_base_sha, out_dir, force=force)
        if not ok:
            stats["errored"] += 1
            continue
        stats["materialized"] += 1

        # Regenerate the patch from the SAME merge_base_sha to head_sha so
        # it applies cleanly on top of the snapshot we just extracted.
        patch_path = _THIS_DIR / f"cases/greptile_{target}/{patch_rel}"
        if _regenerate_patch(fork_dir, merge_base_sha, resolved_head, patch_path):
            stats["patches_regenerated"] += 1
        else:
            logger.warning("    failed to regenerate patch for %s", case.get("id"))
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Clone Greptile forks + materialize per-case bases")
    parser.add_argument("--target", choices=sorted(TARGETS.keys()), help="Process only one target")
    parser.add_argument("--skip-clone", action="store_true", help="Assume forks already cloned")
    parser.add_argument("--force", action="store_true", help="Re-extract snapshots even if present")
    args = parser.parse_args()

    targets = [args.target] if args.target else list(TARGETS.keys())
    grand_total = {"materialized": 0, "skipped": 0, "errored": 0, "patches_regenerated": 0}
    for target in targets:
        slug = TARGETS[target]
        stats = process_target(target, slug, skip_clone=args.skip_clone, force=args.force)
        for k, v in stats.items():
            grand_total[k] += v

    logger.info(
        "Done. materialized=%d patches_regenerated=%d skipped=%d errored=%d",
        grand_total["materialized"],
        grand_total["patches_regenerated"],
        grand_total["skipped"],
        grand_total["errored"],
    )
    return 0 if grand_total["errored"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
