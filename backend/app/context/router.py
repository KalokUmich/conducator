"""Context router — provides code context for AI chat rooms.

Switched from home-built RAG (FAISS + Bedrock Embeddings) to CocoIndex.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/context", tags=["context"])


# ---------------------------------------------------------------------------
# Schemas (local, not worth a separate file)
# ---------------------------------------------------------------------------


class ContextRequest(BaseModel):
    room_id: str
    query:   str = Field(..., description="Natural-language query to search for context.")
    top_k:   int = Field(default=5, ge=1, le=20)


class ContextChunk(BaseModel):
    file_path:   str
    start_line:  int
    end_line:    int
    content:     str
    score:       float
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None


class ContextResponse(BaseModel):
    room_id:  str
    query:    str
    chunks:   List[ContextChunk]
    total:    int


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_code_search_service():
    from backend.app.main import app
    return app.state.code_search_service


def _get_git_workspace_service():
    from backend.app.main import app
    return app.state.git_workspace_service


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/context", response_model=ContextResponse)
async def get_context(
    req: ContextRequest,
    code_search=Depends(_get_code_search_service),
    git_workspace=Depends(_get_git_workspace_service),
) -> ContextResponse:
    """
    Retrieve relevant code context for a room + query.

    Resolves the room's git worktree path, then runs a CocoIndex
    semantic search over the worktree's code.
    """
    # --- 1. Resolve the workspace path for this room ---
    worktree_path = git_workspace.get_worktree_path(req.room_id)
    if worktree_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No workspace found for room_id={req.room_id!r}. "
                   f"Create one via POST /api/git-workspace/workspaces first.",
        )

    # --- 2. Run code search ---
    search_result = await code_search.search(
        query          = req.query,
        workspace_path = str(worktree_path),
        top_k          = req.top_k,
    )

    # --- 3. Map results to context chunks ---
    chunks = [
        ContextChunk(
            file_path   = chunk.file_path,
            start_line  = chunk.start_line,
            end_line    = chunk.end_line,
            content     = chunk.content,
            score       = chunk.score,
            symbol_name = chunk.symbol_name,
            symbol_type = chunk.symbol_type,
        )
        for chunk in search_result.results
    ]

    return ContextResponse(
        room_id = req.room_id,
        query   = req.query,
        chunks  = chunks,
        total   = len(chunks),
    )


@router.get("/context/{room_id}/index-status")
async def get_index_status(
    room_id: str,
    code_search=Depends(_get_code_search_service),
    git_workspace=Depends(_get_git_workspace_service),
) -> Dict[str, Any]:
    """Check whether a workspace's code index is up-to-date."""
    worktree_path = git_workspace.get_worktree_path(room_id)
    if worktree_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No workspace for room {room_id!r}",
        )
    status_obj = code_search.get_index_status(str(worktree_path))
    return status_obj.model_dump()
