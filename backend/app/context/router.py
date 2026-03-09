"""Context router — provides code context for AI chat rooms.

Hybrid retrieval strategy:
  1. **Vector search** (CocoIndex) — semantic similarity to the query
  2. **Graph search** (RepoMap) — structurally important files via PageRank

The graph search is personalised: files found by vector search receive
higher teleportation probability in PageRank, so the graph naturally
returns files that are structurally connected to the semantically
relevant ones.
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
    include_repo_map: bool = Field(default=True, description="Include graph-based repo map.")


class ContextChunk(BaseModel):
    file_path:   str
    start_line:  int
    end_line:    int
    content:     str
    score:       float
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None


class ContextResponse(BaseModel):
    room_id:   str
    query:     str
    chunks:    List[ContextChunk]
    total:     int
    repo_map:  Optional[str] = None   # Graph-based repo map text


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_code_search_service():
    from backend.app.main import app
    return app.state.code_search_service


def _get_git_workspace_service():
    from backend.app.main import app
    return app.state.git_workspace_service


def _get_repo_map_service():
    from backend.app.main import app
    return getattr(app.state, "repo_map_service", None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/context", response_model=ContextResponse)
async def get_context(
    req: ContextRequest,
    code_search=Depends(_get_code_search_service),
    git_workspace=Depends(_get_git_workspace_service),
    repo_map_svc=Depends(_get_repo_map_service),
) -> ContextResponse:
    """
    Retrieve relevant code context for a room + query.

    Uses hybrid retrieval:
      1. Vector search (CocoIndex) for semantic matches
      2. Graph-based repo map (PageRank) for structural context
    """
    # --- 1. Resolve the workspace path for this room ---
    worktree_path = git_workspace.get_worktree_path(req.room_id)
    if worktree_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No workspace found for room_id={req.room_id!r}. "
                   f"Create one via POST /api/git-workspace/workspaces first.",
        )

    # --- 2. Run vector search ---
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

    # --- 4. Generate repo map (if enabled and available) ---
    repo_map_text = None
    if req.include_repo_map and repo_map_svc is not None:
        try:
            # Personalise PageRank to files from vector search
            vector_files = list({c.file_path for c in chunks})
            repo_map_text = repo_map_svc.generate_repo_map(
                workspace_path = str(worktree_path),
                query_files    = vector_files,
            )
        except Exception as exc:
            logger.warning("RepoMap generation failed: %s", exc)
            repo_map_text = None

    return ContextResponse(
        room_id  = req.room_id,
        query    = req.query,
        chunks   = chunks,
        total    = len(chunks),
        repo_map = repo_map_text,
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


@router.get("/context/{room_id}/graph-stats")
async def get_graph_stats(
    room_id: str,
    git_workspace=Depends(_get_git_workspace_service),
    repo_map_svc=Depends(_get_repo_map_service),
) -> Dict[str, Any]:
    """Return dependency graph statistics for a workspace."""
    if repo_map_svc is None:
        return {"available": False, "detail": "RepoMap service not configured"}

    worktree_path = git_workspace.get_worktree_path(room_id)
    if worktree_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No workspace for room {room_id!r}",
        )

    stats = repo_map_svc.get_graph_stats(str(worktree_path))
    return {"available": True, **stats}
