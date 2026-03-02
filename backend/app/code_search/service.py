"""CocoIndex Code Search Service.

Wraps cocoindex-code to provide:
  * Index building (AST-aware chunking + embedding + sqlite-vec storage)
  * Semantic search over code chunks
  * Per-workspace index management
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


class _IndexRecord:
    """Tracks the state of a single workspace index."""

    __slots__ = ("workspace_path", "index_id", "files_count", "chunks_count", "last_updated")

    def __init__(self, workspace_path: str, index_id: str) -> None:
        self.workspace_path = workspace_path
        self.index_id       = index_id
        self.files_count    = 0
        self.chunks_count   = 0
        self.last_updated:  Optional[str] = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CodeSearchService:
    """Manages CocoIndex-based code search across multiple workspaces."""

    def __init__(self) -> None:
        self._index_dir: Path = Path("./cocoindex_data")
        self._embedding_backend: str = "local"
        self._top_k_default: int = 5
        self._indices: Dict[str, _IndexRecord] = {}  # workspace_path → record
        self._initialized: bool = False
        self._cocoindex = None  # lazy import

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, settings) -> None:  # settings: CodeSearchSettings
        """Call once from app lifespan."""
        self._index_dir        = Path(settings.index_dir)
        self._embedding_backend = settings.embedding_backend
        self._top_k_default    = settings.top_k_results
        self._index_dir.mkdir(parents=True, exist_ok=True)

        try:
            import cocoindex  # type: ignore
            self._cocoindex = cocoindex
            logger.info(
                "CocoIndex loaded (backend=%s, index_dir=%s)",
                self._embedding_backend,
                self._index_dir,
            )
        except ImportError:
            logger.warning(
                "cocoindex package not found — CodeSearchService will be degraded. "
                "Install with: pip install cocoindex-code"
            )

        self._initialized = True

    async def shutdown(self) -> None:
        self._initialized = False

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    async def build_index(
        self,
        workspace_path: str,
        force_rebuild: bool = False,
        file_filter: Optional[str] = None,
    ):
        """Build or update the code index for *workspace_path*."""
        from .schemas import IndexBuildResult

        if self._cocoindex is None:
            return IndexBuildResult(
                workspace_path=workspace_path,
                success=False,
                files_indexed=0,
                chunks_indexed=0,
                duration_ms=0.0,
                message="cocoindex package not available",
            )

        start = time.monotonic()
        index_id = self._index_id_for(workspace_path)
        index_db = str(self._index_dir / f"{index_id}.db")

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._cocoindex.build(
                    source_dir    = workspace_path,
                    index_db      = index_db,
                    embedding     = self._embedding_backend,
                    file_filter   = file_filter,
                    force_rebuild = force_rebuild,
                ),
            )
            elapsed = (time.monotonic() - start) * 1000

            record = _IndexRecord(workspace_path=workspace_path, index_id=index_id)
            record.files_count  = getattr(result, "files_indexed", 0)
            record.chunks_count = getattr(result, "chunks_indexed", 0)
            import datetime
            record.last_updated = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._indices[workspace_path] = record

            return IndexBuildResult(
                workspace_path = workspace_path,
                success        = True,
                files_indexed  = record.files_count,
                chunks_indexed = record.chunks_count,
                duration_ms    = elapsed,
                message        = "Index built successfully",
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
        """Return current index status for *workspace_path*."""
        from .schemas import IndexStatusResponse

        record = self._indices.get(workspace_path)
        if record is None:
            return IndexStatusResponse(
                workspace_path=workspace_path,
                indexed=False,
                files_count=0,
                chunks_count=0,
            )
        return IndexStatusResponse(
            workspace_path = workspace_path,
            indexed        = True,
            files_count    = record.files_count,
            chunks_count   = record.chunks_count,
            last_updated   = record.last_updated,
            index_id       = record.index_id,
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
        """Run a semantic code search over the indexed workspace."""
        from .schemas import CodeSearchResponse, CodeChunk

        k = top_k if top_k is not None else self._top_k_default

        if self._cocoindex is None:
            return CodeSearchResponse(
                query=query, results=[], total=0
            )

        record   = self._indices.get(workspace_path)
        index_id = record.index_id if record else self._index_id_for(workspace_path)
        index_db = str(self._index_dir / f"{index_id}.db")

        try:
            raw_results = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._cocoindex.search(
                    query       = query,
                    index_db    = index_db,
                    top_k       = k,
                    file_filter = file_filter,
                ),
            )

            chunks = [
                CodeChunk(
                    file_path   = r.file_path,
                    start_line  = r.start_line,
                    end_line    = r.end_line,
                    content     = r.content,
                    score       = r.score,
                    language    = getattr(r, "language", None),
                    symbol_name = getattr(r, "symbol_name", None),
                    symbol_type = getattr(r, "symbol_type", None),
                )
                for r in raw_results
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
