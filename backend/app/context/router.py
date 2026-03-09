"""Context router — provides code context for AI chat rooms.

Hybrid retrieval strategy:
  1. **Vector search** (CocoIndex) — semantic similarity to the query
  2. **Reranking** (optional) — cross-encoder or API reranker re-scores candidates
  3. **Graph search** (RepoMap) — structurally important files via PageRank

The reranker is a post-retrieval step: vector search returns a larger
candidate set (e.g. 20), the reranker re-scores them, and only the
top-N (e.g. 5) are returned.  This significantly improves precision
without the cost of running an expensive model over the entire corpus.

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
    enable_reranking: Optional[bool] = Field(
        default=None,
        description="Override reranking for this request. None = use server default.",
    )


class ContextChunk(BaseModel):
    file_path:   str
    start_line:  int
    end_line:    int
    content:     str
    score:       float
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None
    rerank_score: Optional[float] = None  # Set when reranking is applied


class ContextResponse(BaseModel):
    room_id:   str
    query:     str
    chunks:    List[ContextChunk]
    total:     int
    repo_map:  Optional[str] = None    # Graph-based repo map text
    reranked:  bool = False             # Whether reranking was applied


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


def _get_rerank_provider():
    from backend.app.main import app
    return getattr(app.state, "rerank_provider", None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/context", response_model=ContextResponse)
async def get_context(
    req: ContextRequest,
    code_search=Depends(_get_code_search_service),
    git_workspace=Depends(_get_git_workspace_service),
    repo_map_svc=Depends(_get_repo_map_service),
    rerank_provider=Depends(_get_rerank_provider),
) -> ContextResponse:
    """
    Retrieve relevant code context for a room + query.

    Uses hybrid retrieval:
      1. Vector search (CocoIndex) for semantic matches
      2. Reranking (optional) for improved precision
      3. Graph-based repo map (PageRank) for structural context
    """
    # --- 1. Resolve the workspace path for this room ---
    worktree_path = git_workspace.get_worktree_path(req.room_id)
    if worktree_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No workspace found for room_id={req.room_id!r}. "
                   f"Create one via POST /api/git-workspace/workspaces first.",
        )

    # --- 2. Determine whether to rerank ---
    should_rerank = False
    if rerank_provider is not None and rerank_provider.name != "none":
        if req.enable_reranking is None:
            should_rerank = True  # Use server default (provider is configured)
        else:
            should_rerank = req.enable_reranking

    # --- 3. Run vector search ---
    # If reranking, fetch more candidates so the reranker has good coverage
    if should_rerank:
        # Fetch a larger candidate set for reranking
        from backend.app.config import load_settings
        try:
            settings = load_settings()
            fetch_k = settings.code_search.rerank_candidates
        except Exception:
            fetch_k = max(req.top_k * 4, 20)
    else:
        fetch_k = req.top_k

    search_result = await code_search.search(
        query          = req.query,
        workspace_path = str(worktree_path),
        top_k          = fetch_k,
    )

    # --- 4. Map results to context chunks ---
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

    # --- 5. Rerank if enabled ---
    reranked = False
    if should_rerank and chunks:
        try:
            documents = [c.content for c in chunks]
            rerank_results = await rerank_provider.rerank(
                query=req.query,
                documents=documents,
                top_n=req.top_k,
            )
            # Rebuild chunks in reranked order
            reranked_chunks = []
            for rr in rerank_results:
                original = chunks[rr.index]
                reranked_chunks.append(
                    ContextChunk(
                        file_path    = original.file_path,
                        start_line   = original.start_line,
                        end_line     = original.end_line,
                        content      = original.content,
                        score        = original.score,
                        symbol_name  = original.symbol_name,
                        symbol_type  = original.symbol_type,
                        rerank_score = rr.score,
                    )
                )
            chunks = reranked_chunks
            reranked = True
            logger.info(
                "Reranked %d candidates → %d results (provider=%s)",
                len(documents),
                len(chunks),
                rerank_provider.name,
            )
        except Exception as exc:
            logger.warning(
                "Reranking failed (provider=%s): %s — returning vector results.",
                rerank_provider.name,
                exc,
            )
            # Fall back to original vector search results, truncated
            chunks = chunks[:req.top_k]
    elif not should_rerank:
        # No reranking — just truncate to top_k
        chunks = chunks[:req.top_k]

    # --- 6. Generate repo map (if enabled and available) ---
    repo_map_text = None
    if req.include_repo_map and repo_map_svc is not None:
        try:
            # Personalise PageRank to files from (reranked) results
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
        reranked = reranked,
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


@router.get("/context/{room_id}/rerank-status")
async def get_rerank_status(
    room_id: str,
    rerank_provider=Depends(_get_rerank_provider),
) -> Dict[str, Any]:
    """Return the status of the reranking provider."""
    if rerank_provider is None:
        return {"available": False, "provider": "none", "detail": "No rerank provider configured"}
    return {"available": True, **rerank_provider.health_check()}
