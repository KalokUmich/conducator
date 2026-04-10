"""One-shot bootstrap for the Greptile benchmark dataset.

This is the **single entry point** for setting up the local Greptile dataset.
It wraps the three lower-level scripts so you don't have to remember the
order or the per-step flags:

  scrape_greptile.py            (Layer A — scrape PR metadata from GitHub)
  import_greptile.py            (Layer B — convert raw JSON → cases.yaml)
  materialize_greptile_bases.py (Layer C — clone forks + extract base snapshots)

Three modes match the three layers (see GREPTILE_BENCHMARK.md for the long story):

  default                                    Layer C only
  ./setup_greptile_dataset.py
    * Uses cases.yaml + patches that are committed to the repo.
    * Clones the 5 ``ai-code-review-evaluation/{target}-greptile`` forks
      anonymously (public repos, no token required).
    * Materializes per-case base snapshots from the merge-base SHA.
    * No GitHub API calls. ~5 min, ~8 GB disk.

  --refresh-import                           Layers B + C
  ./setup_greptile_dataset.py --refresh-import
    * Re-runs the importer against the LOCALLY-CACHED scraped JSON
      (cases/greptile_raw/*.json) and rewrites cases.yaml.
    * Then re-materializes (Layer C) so patches stay in sync with the
      regenerated base SHAs.
    * No token. Use this when you tweak the import_greptile.py heuristics
      (severity inference, line_range window, category mapping, etc.) and
      want the cases.yaml to reflect the new logic.

  --refresh-scrape                           Layers A + B + C
  ./setup_greptile_dataset.py --refresh-scrape
    * Re-scrapes all 50 PRs from GitHub (REQUIRES ``GITHUB_TOKEN`` —
      anonymous quota is 60/h, the scrape needs ~150 calls).
    * Then re-imports + re-materializes.
    * Use this only when Greptile actually updates their benchmark
      (new PRs, force-pushed branches, new bot reviews). Run ~quarterly
      or when they announce a dataset update.

All modes are idempotent — re-running them is safe and skips already-extracted
snapshots unless ``--force`` is set.

Usage::

    cd backend  # so PYTHONPATH picks up app.*
    python ../eval/code_review/setup_greptile_dataset.py
    python ../eval/code_review/setup_greptile_dataset.py --target sentry
    python ../eval/code_review/setup_greptile_dataset.py --refresh-import
    python ../eval/code_review/setup_greptile_dataset.py --refresh-scrape --force

The bootstrapped dataset can then be evaluated with::

    python ../eval/code_review/run.py --brain --provider bedrock \\
        --model "eu.anthropic.claude-sonnet-4-6" \\
        --explorer-model "eu.anthropic.claude-haiku-4-5-20251001-v1:0" \\
        --filter greptile-
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Make sibling modules importable when this script is invoked from anywhere
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

logger = logging.getLogger("setup_greptile_dataset")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def _check_git() -> None:
    if shutil.which("git") is None:
        logger.error("FATAL: ``git`` not found on PATH. Install git first.")
        sys.exit(2)


def _check_disk_space(min_gb: int = 12) -> None:
    """Refuse to start if the eval dir doesn't have ~12 GB free.

    The full dataset uses ~8 GB on disk; we want a 4 GB safety margin so
    one of the larger fork clones doesn't fill the partition mid-run.
    """
    usage = shutil.disk_usage(_THIS_DIR)
    free_gb = usage.free / (1024 ** 3)
    if free_gb < min_gb:
        logger.error(
            "FATAL: only %.1f GB free in %s — need at least %d GB. "
            "Free up space and re-run.",
            free_gb, _THIS_DIR, min_gb,
        )
        sys.exit(2)
    logger.info("Disk: %.1f GB free (need ~%d GB)", free_gb, min_gb)


def _check_token_for_scrape() -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        logger.error(
            "FATAL: --refresh-scrape needs ``GITHUB_TOKEN`` (or ``GH_TOKEN``) "
            "in the environment.\nAnonymous GitHub API quota is 60/hour and "
            "the scraper needs ~150 calls. Create a fine-grained PAT with "
            "*public_repo:read* scope at\n"
            "  https://github.com/settings/personal-access-tokens"
        )
        sys.exit(2)
    logger.info("GitHub token: present (%s…)", token[:10])


def _check_python_modules() -> None:
    """Verify the three sibling scripts exist on disk."""
    for name in ("scrape_greptile.py", "import_greptile.py", "materialize_greptile_bases.py"):
        if not (_THIS_DIR / name).is_file():
            logger.error("FATAL: missing sibling module %s next to setup_greptile_dataset.py", name)
            sys.exit(2)


# ---------------------------------------------------------------------------
# Layer runners
# ---------------------------------------------------------------------------

def _run_scrape(target: Optional[str]) -> None:
    logger.info("\n┌─ Layer A — scrape GitHub ─────────────────────────────────")
    from scrape_greptile import scrape_target, TARGETS, RAW_OUT_DIR
    import json

    targets = [target] if target else list(TARGETS.keys())
    RAW_OUT_DIR.mkdir(parents=True, exist_ok=True)
    for t in targets:
        records = scrape_target(target=t, fetch_diff=False)  # we regenerate diffs locally
        out_path = RAW_OUT_DIR / f"{t.replace('.', '_')}.json"
        out_path.write_text(
            json.dumps([r.to_dict() for r in records], indent=2),
            encoding="utf-8",
        )
        logger.info("  wrote %s (%d PRs)", out_path, len(records))


def _run_import(target: Optional[str]) -> None:
    logger.info("\n┌─ Layer B — import JSON → cases.yaml ──────────────────────")
    from import_greptile import import_target, TARGETS as _T  # noqa: F401
    # The importer's target list comes from whatever raw JSON dumps exist
    if target:
        targets = [target if "." not in target else target.replace(".", "_")]
    else:
        # Discover from raw dir
        from scrape_greptile import RAW_OUT_DIR as _RAW
        targets = sorted(p.stem.replace("_", ".") if p.stem == "cal_com" else p.stem
                         for p in _RAW.glob("*.json"))
        # Re-canonicalise: cal_com → cal.com (the importer uses dotted form)
        targets = [t if t != "cal.com" else "cal.com" for t in targets]
        # Ensure we use the slug expected by import_greptile.import_target
        targets = [p.stem.replace("_", ".") if p.stem == "cal_com" else p.stem
                   for p in _RAW.glob("*.json")]
        targets = [(t if t != "cal_com" else "cal.com") for t in targets]
    for t in targets:
        cases, skipped, patches = import_target(t)
        logger.info("  %s -> %d cases (%d skipped, %d patches written)",
                    t, cases, skipped, patches)


def _run_materialize(target: Optional[str], force: bool, skip_clone: bool) -> None:
    logger.info("\n┌─ Layer C — clone forks + materialize bases ───────────────")
    from materialize_greptile_bases import process_target, TARGETS as MATERIALIZE_TARGETS
    targets = [target] if target else list(MATERIALIZE_TARGETS.keys())
    grand = {"materialized": 0, "skipped": 0, "errored": 0, "patches_regenerated": 0}
    for t in targets:
        slug = MATERIALIZE_TARGETS.get(t)
        if not slug:
            logger.warning("  unknown target %r — skipping", t)
            continue
        stats = process_target(target=t, slug=slug, skip_clone=skip_clone, force=force)
        for k, v in stats.items():
            grand[k] += v
    logger.info(
        "  totals: materialized=%d patches_regenerated=%d skipped=%d errored=%d",
        grand["materialized"], grand["patches_regenerated"],
        grand["skipped"], grand["errored"],
    )
    if grand["errored"]:
        logger.error("  -> %d cases errored, see log above", grand["errored"])
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap the Greptile benchmark dataset locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--refresh-import", action="store_true",
        help="Re-run the importer (Layer B) before materializing. No token needed.",
    )
    mode.add_argument(
        "--refresh-scrape", action="store_true",
        help="Full re-scrape from GitHub (Layers A+B+C). Requires GITHUB_TOKEN.",
    )
    parser.add_argument(
        "--target",
        choices=["sentry", "cal_com", "grafana", "keycloak", "discourse", "cal.com"],
        help="Process only one target instead of all 5.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-extract base snapshots even if they already exist on disk.",
    )
    parser.add_argument(
        "--skip-clone", action="store_true",
        help="Skip cloning forks (assume they're already present).",
    )
    args = parser.parse_args()

    # Normalize cal.com canonicalisation
    target = args.target
    if target == "cal.com":
        target = "cal_com"

    # ----- pre-flight -----
    logger.info("=" * 60)
    logger.info("Greptile benchmark dataset bootstrap")
    logger.info("=" * 60)
    _check_git()
    _check_python_modules()
    _check_disk_space()

    do_scrape = args.refresh_scrape
    do_import = args.refresh_scrape or args.refresh_import
    do_materialize = True  # always

    if do_scrape:
        _check_token_for_scrape()

    logger.info(
        "Mode: scrape=%s import=%s materialize=%s target=%s force=%s",
        do_scrape, do_import, do_materialize, target or "all", args.force,
    )

    # ----- run layers in order -----
    if do_scrape:
        # Pass the dotted form to the scraper (its TARGETS dict uses cal.com)
        _run_scrape(target.replace("_", ".") if target == "cal_com" else target)
    if do_import:
        _run_import(target.replace("_", ".") if target == "cal_com" else target)
    _run_materialize(target=target, force=args.force, skip_clone=args.skip_clone)

    # ----- summary -----
    logger.info("\n" + "=" * 60)
    logger.info("Done. Next step:")
    logger.info("")
    logger.info("  cd backend")
    logger.info("  python ../eval/code_review/run.py --brain --provider bedrock \\")
    logger.info('      --model "eu.anthropic.claude-sonnet-4-6" \\')
    logger.info('      --explorer-model "eu.anthropic.claude-haiku-4-5-20251001-v1:0" \\')
    logger.info("      --filter greptile-")
    logger.info("")
    logger.info("See GREPTILE_BENCHMARK.md for the full eval guide.")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
