"""Subprocess worker: index a single workspace using cocoindex_code.

This script must be run as a subprocess (not imported directly) so that
environment variables set by the caller take effect *before* cocoindex_code
modules are imported (they read config at module level from env vars).

Required env vars (set by the caller before launching this script):
  COCOINDEX_CODE_ROOT_PATH       — absolute path to the workspace to index
  COCOINDEX_CODE_EMBEDDING_MODEL — LiteLLM model string or "sbert/..." prefix

Optional env vars:
  COCOINDEX_DATABASE_URL         — Postgres URL for incremental state tracking
                                   (omit for SQLite-based state tracking)
  _COCOINDEX_DROP_FIRST          — "1" to drop the existing index before rebuild

Output (stdout): JSON line  {"success": true, "files": N, "chunks": N}
Errors  (stderr): JSON line  {"success": false, "error": "..."}
Exit code: 0 on success, 1 on failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys


def _count_db(db_path: str) -> tuple[int, int]:
    """Return (files_count, chunks_count) from the sqlite-vec DB, or (0,0)."""
    try:
        conn = sqlite3.connect(db_path)
        chunks = conn.execute("SELECT COUNT(*) FROM code_chunks_vec").fetchone()[0]
        files  = conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM code_chunks_vec"
        ).fetchone()[0]
        conn.close()
        return files, chunks
    except Exception:
        return 0, 0


def main() -> None:
    workspace_path = os.environ.get("COCOINDEX_CODE_ROOT_PATH", "")
    if not workspace_path:
        _fail("COCOINDEX_CODE_ROOT_PATH is not set")
        return

    drop_first = os.environ.get("_COCOINDEX_DROP_FIRST", "") in ("1", "true", "yes")

    # Diagnostic: dump what the worker actually sees
    _diag = {
        "COCOINDEX_CODE_EMBEDDING_MODEL": os.environ.get("COCOINDEX_CODE_EMBEDDING_MODEL"),
        "COCOINDEX_CODE_ROOT_PATH": workspace_path,
        "AWS_DEFAULT_REGION": os.environ.get("AWS_DEFAULT_REGION"),
        "python": sys.executable,
    }
    print(json.dumps({"_diag": _diag}), file=sys.stderr)

    try:
        # Imports happen AFTER env vars are set — this is why we run as subprocess.
        from cocoindex_code.indexer import app  # noqa: PLC0415
        from cocoindex_code.config import config  # noqa: PLC0415
        import cocoindex as coco  # noqa: PLC0415

        print(json.dumps({
            "_diag_config": {
                "embedding_model": config.embedding_model,
                "index_dir": str(config.index_dir),
            }
        }), file=sys.stderr)

        async def _run() -> None:
            await coco.start()
            try:
                if drop_first:
                    await app.drop()
                await app.update()
            finally:
                await coco.stop()

        asyncio.run(_run())

        db_path = str(config.target_sqlite_db_path)
        files, chunks = _count_db(db_path)
        print(json.dumps({"success": True, "files": files, "chunks": chunks, "db_path": db_path}))

    except Exception as exc:  # pylint: disable=broad-except
        _fail(str(exc))


def _fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()

