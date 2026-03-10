"""RAG router — bridges the VS Code extension's RagClient to CodeSearchService.

The extension's ``RagClient`` calls three endpoints:
    POST /rag/reindex   — first batch: clear + full rebuild
    POST /rag/index     — subsequent batches: incremental update
    POST /rag/search    — semantic code search

File contents are received from the extension and written to a per-workspace
scratch directory so that ``CodeSearchService.build_index()`` can pick them up
via the cocoindex-code subprocess.

Scratch directory layout::

    {code_search.index_dir}/rag_upload/{workspace_id}/
        src/
            <relative file paths>
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class RagFileChange(BaseModel):
    path:    str
    content: str
    action:  Literal["upsert", "delete"]


class RagIndexRequest(BaseModel):
    workspace_id: str
    files:        List[RagFileChange]


class RagIndexResponse(BaseModel):
    chunks_added:    int
    chunks_removed:  int
    files_processed: int


class RagSearchFilters(BaseModel):
    language:     Optional[str] = None
    file_pattern: Optional[str] = None


class RagSearchRequest(BaseModel):
    workspace_id: str
    query:        str
    top_k:        Optional[int] = 5
    filters:      Optional[RagSearchFilters] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_scratch_dir(workspace_id: str) -> Path:
    """Return (and create) the scratch directory for this workspace."""
    from app.config import load_settings
    settings = load_settings()
    root = Path(settings.code_search.index_dir) / "rag_upload" / workspace_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_files(scratch: Path, files: List[RagFileChange]) -> int:
    """Write/delete files in *scratch*.  Returns count of upserted files."""
    count = 0
    for f in files:
        dest = scratch / Path(f.path.lstrip("/"))
        if f.action == "delete":
            if dest.exists():
                dest.unlink()
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(f.content, encoding="utf-8", errors="replace")
            count += 1
    return count


def _get_code_search_service():
    from app.main import app
    return app.state.code_search_service


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/reindex", response_model=RagIndexResponse)
async def rag_reindex(req: RagIndexRequest) -> RagIndexResponse:
    """Full re-index for *workspace_id*.

    Clears existing scratch files, writes all received files, then triggers a
    forced rebuild of the code index.  Called for the first batch of files at
    session start.
    """
    scratch = _get_scratch_dir(req.workspace_id)

    # Clear old content so the index reflects the current workspace state.
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)

    files_written = _write_files(scratch, req.files)
    logger.info("[RAG] reindex workspace=%s files=%d", req.workspace_id, files_written)

    svc = _get_code_search_service()
    result = await svc.build_index(str(scratch), force_rebuild=True)

    return RagIndexResponse(
        chunks_added    = result.chunks_indexed if result.success else 0,
        chunks_removed  = 0,
        files_processed = files_written,
    )


@router.post("/index", response_model=RagIndexResponse)
async def rag_index(req: RagIndexRequest) -> RagIndexResponse:
    """Incremental index update for *workspace_id*.

    Writes/deletes changed files in the scratch directory, then triggers an
    incremental rebuild.  Called for all batches after the first reindex.
    """
    scratch = _get_scratch_dir(req.workspace_id)
    files_written = _write_files(scratch, req.files)
    logger.info("[RAG] index workspace=%s files=%d", req.workspace_id, files_written)

    svc = _get_code_search_service()
    result = await svc.build_index(str(scratch), force_rebuild=False)

    return RagIndexResponse(
        chunks_added    = result.chunks_indexed if result.success else 0,
        chunks_removed  = 0,
        files_processed = files_written,
    )


@router.post("/search")
async def rag_search(req: RagSearchRequest) -> dict:
    """Semantic code search over an indexed workspace."""
    scratch = _get_scratch_dir(req.workspace_id)
    file_filter = req.filters.file_pattern if req.filters else None

    svc = _get_code_search_service()
    response = await svc.search(
        query          = req.query,
        workspace_path = str(scratch),
        top_k          = req.top_k or 5,
        file_filter    = file_filter,
    )
    return {
        "query":   response.query,
        "results": [r.model_dump() for r in response.results],
        "total":   response.total,
    }

