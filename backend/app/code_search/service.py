"""CocoIndex Code Search Service.

Wraps cocoindex-code to provide:
  * Index building (AST-aware chunking + embedding + vector storage)
  * Semantic search over code chunks
  * Per-workspace index management
  * Incremental processing (only re-index changed files)

Architecture
------------
Indexing uses ``cocoindex-code`` as a **subprocess** (one per workspace).
This sidesteps the module-level singleton problem: cocoindex-code reads its
config from env vars at import time, so each subprocess can have a different
``COCOINDEX_CODE_ROOT_PATH`` without interfering with others.

Search bypasses the subprocess entirely: we embed the query ourselves using
our ``EmbeddingProvider`` and query the sqlite-vec database that
cocoindex-code writes to (``{workspace}/.cocoindex_code/target_sqlite.db``).

Storage backends
----------------
* **sqlite** (default) — embedded, no setup required.
  Both the vector index and cocoindex's state DB live inside the workspace
  under ``.cocoindex_code/``.
* **postgres** — enables incremental re-indexing. Setting
  ``COCOINDEX_DATABASE_URL`` in the worker subprocess causes cocoindex to
  store its *state-tracking* (lineage) in Postgres instead of the local
  SQLite file. The **vector index** still lives in sqlite-vec — that is how
  cocoindex-code is designed. This means only changed files are re-processed
  on subsequent ``build_index`` calls.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .embedding_provider import EmbeddingProvider, create_embedding_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# sqlite-vec helper functions
# ---------------------------------------------------------------------------
# cocoindex-code always stores the vector index at:
#   {workspace}/.cocoindex_code/target_sqlite.db
#
# Schema (Vec0 virtual table):
#   code_chunks_vec(id, file_path, language, content,
#                   start_line, end_line, embedding)
#
# sqlite-vec uses *binary* BLOB embeddings (little-endian float32 array).
# ---------------------------------------------------------------------------


def _sqlite_count(db_path: str) -> tuple[int, int]:
    """Return (files_count, chunks_count) from the sqlite-vec DB."""
    try:
        import sqlite_vec  # type: ignore  # noqa: F401
        conn = sqlite3.connect(db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        chunks = conn.execute("SELECT COUNT(*) FROM code_chunks_vec").fetchone()[0]
        files  = conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM code_chunks_vec"
        ).fetchone()[0]
        conn.close()
        return files, chunks
    except Exception:
        return 0, 0


def _l2_to_score(distance: float) -> float:
    """Convert L2 distance to cosine-similarity-like score (exact for unit vecs)."""
    return 1.0 - distance * distance / 2.0


def _sqlite_knn_search(
    db_path: str,
    query_vec,        # np.ndarray[float32]
    k: int,
    file_filter: Optional[str],
) -> List[Dict]:
    """Query the sqlite-vec KNN index and return raw row dicts.

    Vec0 virtual tables CANNOT filter on auxiliary columns (like file_path)
    inside a MATCH query — that triggers a "query too complex" error.
    When a file_filter is given we fall back to a full-scan with
    ``vec_distance_L2``, exactly like cocoindex-code's own ``query.py`` does.
    """
    import sqlite_vec  # type: ignore  # noqa: F401

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    # sqlite-vec expects raw bytes (float32, little-endian)
    vec_bytes = query_vec.astype("float32").tobytes()

    if file_filter:
        # Full scan with SQL-level distance + GLOB filter on file_path.
        # Vec0 index can't filter auxiliary columns, so we use vec_distance_L2.
        rows = conn.execute(
            """
            SELECT file_path, language, content, start_line, end_line,
                   vec_distance_L2(embedding, ?) as distance
            FROM code_chunks_vec
            WHERE file_path GLOB ?
            ORDER BY distance
            LIMIT ?
            """,
            (vec_bytes, file_filter, k),
        ).fetchall()
    else:
        # Fast KNN path via Vec0 index — no auxiliary-column filtering.
        rows = conn.execute(
            """
            SELECT file_path, language, content, start_line, end_line, distance
            FROM code_chunks_vec
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (vec_bytes, k),
        ).fetchall()

    conn.close()

    return [
        {
            "file_path":  row[0],
            "language":   row[1],
            "content":    row[2],
            "start_line": row[3],
            "end_line":   row[4],
            "score":      _l2_to_score(row[5]),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


class _IndexRecord:
    """Tracks the state of a single workspace index."""

    __slots__ = (
        "workspace_path",
        "index_id",
        "files_count",
        "chunks_count",
        "last_updated",
        "is_incremental",
    )

    def __init__(self, workspace_path: str, index_id: str) -> None:
        self.workspace_path = workspace_path
        self.index_id       = index_id
        self.files_count    = 0
        self.chunks_count   = 0
        self.last_updated:  Optional[str] = None
        self.is_incremental = False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CodeSearchService:
    """Manages CocoIndex-based code search across multiple workspaces."""

    def __init__(self) -> None:
        self._index_dir: Path = Path("./cocoindex_data")
        self._embedding_model: str = "bedrock/eu.cohere.embed-v4:0"
        self._top_k_default: int = 5
        self._indices: Dict[str, _IndexRecord] = {}  # workspace_path → record
        self._initialized: bool = False
        self._cocoindex = None  # lazy import
        self._embedding_provider: Optional[EmbeddingProvider] = None

        # Postgres / incremental processing
        self._storage_backend: str = "sqlite"
        self._postgres_url: Optional[str] = None
        self._incremental: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, settings, secrets=None) -> None:
        """Call once from app lifespan.

        Parameters
        ----------
        settings:
            ``CodeSearchSettings`` from config.
        secrets:
            ``Secrets`` object for credential injection (optional).
        """
        self._index_dir = Path(settings.index_dir)
        self._top_k_default = settings.top_k_results
        self._index_dir.mkdir(parents=True, exist_ok=True)

        # Resolve embedding model string
        self._embedding_model = getattr(settings, "embedding_model", None)
        if self._embedding_model is None:
            # Legacy fallback
            from .embedding_provider import _legacy_backend_to_model
            backend = getattr(settings, "embedding_backend", "local")
            self._embedding_model = _legacy_backend_to_model(backend, settings)

        # Storage backend
        self._storage_backend = getattr(settings, "storage_backend", "sqlite")
        # postgres_url: prefer settings field, fall back to env var injected by
        # _inject_embedding_env_vars (which reads from secrets.database.url)
        self._postgres_url = (
            getattr(settings, "postgres_url", None)
            or os.environ.get("COCOINDEX_DATABASE_URL")
        )
        self._incremental = getattr(settings, "incremental", False)

        # Set env var so cocoindex-code subprocess uses the same embedding model
        os.environ["COCOINDEX_CODE_EMBEDDING_MODEL"] = self._embedding_model

        # Postgres: set COCOINDEX_DATABASE_URL so the worker subprocess uses it
        # for state-tracking (enables incremental processing).
        if self._storage_backend == "postgres" and self._postgres_url:
            os.environ["COCOINDEX_DATABASE_URL"] = self._postgres_url

        # Create embedding provider (used for search queries)
        try:
            self._embedding_provider = create_embedding_provider(settings)
            logger.info(
                "Embedding provider created: %s (dims=%d)",
                self._embedding_provider.name,
                self._embedding_provider.dimensions,
            )
        except Exception as exc:
            logger.warning(
                "Failed to create embedding provider (%s): %s — "
                "code search will be degraded.",
                self._embedding_model,
                exc,
            )

        # Mark cocoindex-code as available if the package is installed
        try:
            import cocoindex_code  # type: ignore  # noqa: F401
            self._cocoindex = True  # sentinel: package is available
            logger.info(
                "cocoindex-code available (model=%s, storage=%s, incremental=%s)",
                self._embedding_model,
                self._storage_backend,
                self._incremental,
            )
        except ImportError:
            self._cocoindex = None
            logger.warning(
                "cocoindex-code package not found — code search degraded. "
                "Install with: pip install cocoindex-code sqlite-vec"
            )

        self._initialized = True

    async def shutdown(self) -> None:
        self._initialized = False

    @property
    def embedding_provider(self) -> Optional[EmbeddingProvider]:
        """Access the configured embedding provider (may be None if init failed)."""
        return self._embedding_provider

    @property
    def storage_backend(self) -> str:
        """Return the current storage backend ('sqlite' or 'postgres')."""
        return self._storage_backend

    @property
    def is_incremental(self) -> bool:
        """Whether incremental processing is enabled."""
        return self._incremental and self._storage_backend == "postgres"

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    async def build_index(
        self,
        workspace_path: str,
        force_rebuild: bool = False,
        file_filter: Optional[str] = None,
    ):
        """Build or update the code index for *workspace_path*.

        Runs ``_indexer_worker.py`` as a subprocess so that every workspace
        gets its own isolated environment (cocoindex-code reads config from
        env vars at import time — a single-process approach can't switch
        workspaces without restarting).

        When Postgres is configured, cocoindex uses it for state tracking,
        enabling incremental re-indexing (only changed files are re-processed).
        The vector index itself always lives in the workspace's own
        ``{workspace}/.cocoindex_code/target_sqlite.db``.
        """
        from .schemas import IndexBuildResult

        if self._cocoindex is None:
            return IndexBuildResult(
                workspace_path=workspace_path,
                success=False,
                files_indexed=0,
                chunks_indexed=0,
                duration_ms=0.0,
                message="cocoindex-code package not available",
            )

        start = time.monotonic()
        index_id = self._index_id_for(workspace_path)

        # Build env for the worker subprocess (inherits current env, then overrides)
        worker_env = os.environ.copy()
        worker_env["COCOINDEX_CODE_ROOT_PATH"]       = workspace_path
        worker_env["COCOINDEX_CODE_EMBEDDING_MODEL"] = self._embedding_model
        if force_rebuild:
            worker_env["_COCOINDEX_DROP_FIRST"] = "1"
        else:
            worker_env.pop("_COCOINDEX_DROP_FIRST", None)

        if self._storage_backend == "postgres" and self._postgres_url:
            worker_env["COCOINDEX_DATABASE_URL"] = self._postgres_url
        else:
            # Remove any Postgres URL so the worker falls back to local SQLite state
            worker_env.pop("COCOINDEX_DATABASE_URL", None)

        worker_script = Path(__file__).parent / "_indexer_worker.py"

        logger.info(
            "Launching indexer worker: embedding_model=%r, python=%s, workspace=%s",
            worker_env.get("COCOINDEX_CODE_EMBEDDING_MODEL"),
            sys.executable,
            workspace_path,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(worker_script),
                env=worker_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            elapsed = (time.monotonic() - start) * 1000

            if proc.returncode != 0:
                raw_stderr = stderr_bytes.decode(errors="replace").strip()
                raw_stdout = stdout_bytes.decode(errors="replace").strip()
                logger.error(
                    "Index worker exited %d for %s\n  STDERR: %s\n  STDOUT: %s",
                    proc.returncode, workspace_path, raw_stderr, raw_stdout,
                )
                err_msg = raw_stderr
                # Try to extract JSON error message
                try:
                    err_data = json.loads(raw_stderr)
                    err_msg = err_data.get("error", raw_stderr)
                except Exception:
                    pass
                return IndexBuildResult(
                    workspace_path=workspace_path,
                    success=False,
                    files_indexed=0,
                    chunks_indexed=0,
                    duration_ms=elapsed,
                    message=err_msg,
                )

            # Parse worker JSON output
            out_text = stdout_bytes.decode(errors="replace").strip()
            try:
                out_data = json.loads(out_text)
            except Exception:
                out_data = {}

            files_count  = out_data.get("files", 0)
            chunks_count = out_data.get("chunks", 0)
            is_incremental = (
                self._incremental
                and self._storage_backend == "postgres"
                and not force_rebuild
            )

            record = _IndexRecord(workspace_path=workspace_path, index_id=index_id)
            record.files_count    = files_count
            record.chunks_count   = chunks_count
            record.is_incremental = is_incremental
            record.last_updated   = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._indices[workspace_path] = record

            return IndexBuildResult(
                workspace_path=workspace_path,
                success=True,
                files_indexed=files_count,
                chunks_indexed=chunks_count,
                duration_ms=elapsed,
                message=(
                    "Incremental update completed"
                    if is_incremental
                    else "Index built successfully"
                ),
            )

        except Exception as exc:  # pylint: disable=broad-except
            elapsed = (time.monotonic() - start) * 1000
            logger.error("Index build failed for %s: %s", workspace_path, exc)
            return IndexBuildResult(
                workspace_path=workspace_path,
                success=False,
                files_indexed=0,
                chunks_indexed=0,
                duration_ms=elapsed,
                message=str(exc),
            )

    def get_index_status(self, workspace_path: str):
        """Return current index status for *workspace_path*.

        Also checks the sqlite-vec DB on disk so the status reflects reality
        even if ``build_index`` was not called in this process lifetime.
        """
        from .schemas import IndexStatusResponse

        # The vector DB is always written inside the workspace by cocoindex-code
        db_path = Path(workspace_path) / ".cocoindex_code" / "target_sqlite.db"
        on_disk = db_path.exists()

        record = self._indices.get(workspace_path)
        if record is None and not on_disk:
            return IndexStatusResponse(
                workspace_path=workspace_path,
                indexed=False,
                files_count=0,
                chunks_count=0,
            )

        # Read live counts from DB if record is stale / missing
        if on_disk and (record is None or record.files_count == 0):
            files, chunks = _sqlite_count(str(db_path))
        else:
            files  = record.files_count  if record else 0
            chunks = record.chunks_count if record else 0

        return IndexStatusResponse(
            workspace_path=workspace_path,
            indexed=on_disk,
            files_count=files,
            chunks_count=chunks,
            last_updated=record.last_updated if record else None,
            index_id=record.index_id if record else self._index_id_for(workspace_path),
            storage_backend=self._storage_backend,
            is_incremental=record.is_incremental if record else False,
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query:          str,
        workspace_path: str,
        top_k:          Optional[int] = None,
        file_filter:    Optional[str] = None,
    ):
        """Run a semantic code search over the indexed workspace.

        Embeds the query with our own ``EmbeddingProvider`` then directly
        queries the sqlite-vec database that cocoindex-code writes to
        (``{workspace}/.cocoindex_code/target_sqlite.db``).
        This avoids any subprocess overhead for read-only operations.
        """
        from .schemas import CodeSearchResponse, CodeChunk

        k = top_k if top_k is not None else self._top_k_default

        if self._embedding_provider is None:
            logger.warning("No embedding provider — code search returning empty results")
            return CodeSearchResponse(query=query, results=[], total=0)

        db_path = Path(workspace_path) / ".cocoindex_code" / "target_sqlite.db"
        if not db_path.exists():
            logger.warning(
                "Index DB not found for workspace %s — run build_index first",
                workspace_path,
            )
            return CodeSearchResponse(query=query, results=[], total=0)

        record   = self._indices.get(workspace_path)
        index_id = record.index_id if record else self._index_id_for(workspace_path)

        try:
            # 1. Embed the query
            query_vec = await self._embedding_provider.embed_query(query)

            # 2. Query sqlite-vec (run in thread-pool to avoid blocking event loop)
            loop = asyncio.get_event_loop()
            rows = await loop.run_in_executor(
                None,
                lambda: _sqlite_knn_search(str(db_path), query_vec, k, file_filter),
            )

            chunks = [
                CodeChunk(
                    file_path  = row["file_path"],
                    start_line = row["start_line"],
                    end_line   = row["end_line"],
                    content    = row["content"],
                    score      = row["score"],
                    language   = row.get("language"),
                )
                for row in rows
            ]
            return CodeSearchResponse(
                query=query, results=chunks, total=len(chunks), index_id=index_id
            )

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Code search failed for workspace %s: %s", workspace_path, exc)
            return CodeSearchResponse(query=query, results=[], total=0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _index_id_for(workspace_path: str) -> str:
        import hashlib
        return hashlib.sha256(workspace_path.encode()).hexdigest()[:16]
