"""Agent loop router — replaces the old hybrid retrieval context endpoint.

Provides:
  POST /api/context/query  — run an agent loop to answer a code question
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .service import AgentEvent, AgentLoopService, AgentResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/context", tags=["context"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ContextQueryRequest(BaseModel):
    room_id: str
    query: str = Field(..., description="Natural-language question about the codebase.")
    max_iterations: int = Field(default=15, ge=1, le=30)
    model_id: Optional[str] = Field(
        default=None,
        description="Override model for this request. Uses default if null.",
    )


class ContextChunkResponse(BaseModel):
    file_path: str
    content: str
    start_line: int = 0
    end_line: int = 0
    source_tool: str = ""


class ContextQueryResponse(BaseModel):
    room_id: str
    query: str
    answer: str
    context_chunks: List[ContextChunkResponse]
    tool_calls_made: int
    iterations: int
    duration_ms: float
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_git_workspace_service():
    from app.main import app
    return app.state.git_workspace_service


def _get_agent_provider():
    """Get the AI provider configured for agent loop."""
    from app.main import app
    return getattr(app.state, "agent_provider", None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/query", response_model=ContextQueryResponse)
async def context_query(
    req: ContextQueryRequest,
    git_workspace=Depends(_get_git_workspace_service),
    agent_provider=Depends(_get_agent_provider),
) -> ContextQueryResponse:
    """Run an agent loop to find relevant code context and answer a question."""
    if agent_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No AI provider configured. Enable an AI provider in conductor.settings.yaml.",
        )

    worktree_path = git_workspace.get_worktree_path(req.room_id)
    if worktree_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No workspace for room_id={req.room_id!r}.",
        )

    agent = AgentLoopService(
        provider=agent_provider,
        max_iterations=req.max_iterations,
    )

    result: AgentResult = await agent.run(
        query=req.query,
        workspace_path=str(worktree_path),
    )

    chunks = [
        ContextChunkResponse(
            file_path=c.file_path,
            content=c.content,
            start_line=c.start_line,
            end_line=c.end_line,
            source_tool=c.source_tool,
        )
        for c in result.context_chunks
    ]

    return ContextQueryResponse(
        room_id=req.room_id,
        query=req.query,
        answer=result.answer,
        context_chunks=chunks,
        tool_calls_made=result.tool_calls_made,
        iterations=result.iterations,
        duration_ms=result.duration_ms,
        error=result.error,
    )


@router.post("/query/stream")
async def context_query_stream(
    req: ContextQueryRequest,
    git_workspace=Depends(_get_git_workspace_service),
    agent_provider=Depends(_get_agent_provider),
):
    """SSE streaming version of context_query.

    Streams events as the agent loop progresses so the client can display
    real-time progress (e.g. "Searching for auth patterns…").

    Event types:
      * ``thinking``      — LLM reasoning text
      * ``tool_call``     — tool invocation starting
      * ``tool_result``   — tool execution completed (with summary)
      * ``context_chunk`` — a piece of code context collected
      * ``done``          — final answer
      * ``error``         — unrecoverable error
    """
    if agent_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No AI provider configured. Enable an AI provider in conductor.settings.yaml.",
        )

    worktree_path = git_workspace.get_worktree_path(req.room_id)
    if worktree_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No workspace for room_id={req.room_id!r}.",
        )

    agent = AgentLoopService(
        provider=agent_provider,
        max_iterations=req.max_iterations,
    )

    async def event_generator():
        async for event in agent.run_stream(
            query=req.query,
            workspace_path=str(worktree_path),
        ):
            yield f"event: {event.kind}\ndata: {json.dumps(event.data, default=str)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
