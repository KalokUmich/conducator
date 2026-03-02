"""FastAPI router for the Git Workspace module.

All endpoints are under the /api/git-workspace prefix (registered in main.py).
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status

from .schemas import (
    CredentialPayload,
    DelegateAuthRequest,
    DelegateAuthResponse,
    GitWorkspaceHealth,
    WorkspaceCommitRequest,
    WorkspaceCommitResult,
    WorkspaceCreateRequest,
    WorkspaceDestroyResult,
    WorkspaceInfo,
    WorkspacePushRequest,
    WorkspacePushResult,
    WorkspaceSyncRequest,
    WorkspaceSyncResult,
)
from .service import GitWorkspaceService
from .delegate_broker import DelegateBroker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/git-workspace", tags=["git-workspace"])

# Service instances are injected via FastAPI dependency injection.
# In main.py, a single instance is created at startup and stored in app.state.


def get_git_service(  # pragma: no cover
    # This will be overridden in tests via app.dependency_overrides
) -> GitWorkspaceService:
    from backend.app.main import app  # lazy import to avoid circular dependency
    return app.state.git_workspace_service


def get_delegate_broker() -> DelegateBroker:  # pragma: no cover
    from backend.app.main import app
    return app.state.delegate_broker


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=GitWorkspaceHealth)
async def health(
    svc: GitWorkspaceService = Depends(get_git_service),
) -> GitWorkspaceHealth:
    """Basic health check for the git workspace module."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=5
        )
        git_version = result.stdout.strip()
    except Exception as exc:  # pylint: disable=broad-except
        return GitWorkspaceHealth(
            status="error",
            active_rooms=0,
            git_version="unknown",
            detail=str(exc),
        )

    workspaces = svc.list_workspaces()
    return GitWorkspaceHealth(
        status="ok",
        active_rooms=len(workspaces),
        git_version=git_version,
    )


@router.post("/workspaces", response_model=WorkspaceInfo, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    req: WorkspaceCreateRequest,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> WorkspaceInfo:
    """Create a new git-backed workspace for a room."""
    try:
        return await svc.create_workspace(req)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/workspaces", response_model=List[WorkspaceInfo])
async def list_workspaces(
    svc: GitWorkspaceService = Depends(get_git_service),
) -> List[WorkspaceInfo]:
    """List all active workspaces."""
    return svc.list_workspaces()


@router.get("/workspaces/{room_id}", response_model=WorkspaceInfo)
async def get_workspace(
    room_id: str,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> WorkspaceInfo:
    """Get details for a specific workspace."""
    info = svc.get_workspace(room_id)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No workspace found for room_id={room_id!r}",
        )
    return info


@router.post("/workspaces/{room_id}/credentials")
async def upload_credentials(
    room_id: str,
    payload: CredentialPayload,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> dict:
    """Upload (or replace) credentials for a workspace (Mode A)."""
    await svc.store_credentials(room_id, payload)
    return {"status": "ok", "room_id": room_id}


@router.delete("/workspaces/{room_id}/credentials")
async def revoke_credentials(
    room_id: str,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> dict:
    """Revoke stored credentials for a workspace."""
    await svc.revoke_credentials(room_id)
    return {"status": "ok", "room_id": room_id}


@router.post("/workspaces/{room_id}/sync", response_model=WorkspaceSyncResult)
async def sync_workspace(
    room_id: str,
    req: WorkspaceSyncRequest,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> WorkspaceSyncResult:
    """Pull the latest changes from remote into the worktree."""
    req.room_id = room_id
    return await svc.sync_workspace(req)


@router.post("/workspaces/{room_id}/commit", response_model=WorkspaceCommitResult)
async def commit_workspace(
    room_id: str,
    req: WorkspaceCommitRequest,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> WorkspaceCommitResult:
    """Stage all changes and create a commit."""
    req.room_id = room_id
    return await svc.commit_workspace(req)


@router.post("/workspaces/{room_id}/push", response_model=WorkspacePushResult)
async def push_workspace(
    room_id: str,
    req: WorkspacePushRequest,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> WorkspacePushResult:
    """Push the worktree branch to the remote."""
    req.room_id = room_id
    return await svc.push_workspace(req)


@router.delete("/workspaces/{room_id}", response_model=WorkspaceDestroyResult)
async def destroy_workspace(
    room_id: str,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> WorkspaceDestroyResult:
    """Destroy a workspace and clean up the worktree."""
    return await svc.destroy_workspace(room_id)


# ---------------------------------------------------------------------------
# WebSocket — file-sync stream
# ---------------------------------------------------------------------------


@router.websocket("/ws/{room_id}/file-sync")
async def file_sync_ws(
    websocket: WebSocket,
    room_id: str,
    svc: GitWorkspaceService = Depends(get_git_service),
) -> None:
    """WebSocket endpoint for real-time file-sync events."""
    await websocket.accept()

    async def _send_event(event) -> None:
        await websocket.send_json(event.model_dump(mode="json"))

    svc.register_broadcast(room_id, _send_event)
    try:
        while True:
            await websocket.receive_text()  # keep-alive / heartbeat
    except WebSocketDisconnect:
        logger.info("file-sync WS disconnected: room=%s", room_id)
    finally:
        svc.unregister_broadcast(room_id, _send_event)


# ---------------------------------------------------------------------------
# WebSocket — credential delegation (Mode B)
# ---------------------------------------------------------------------------


@router.websocket("/ws/{room_id}/delegate-auth")
async def delegate_auth_ws(
    websocket: WebSocket,
    room_id: str,
    broker: DelegateBroker = Depends(get_delegate_broker),
) -> None:
    """WebSocket endpoint for Mode B credential delegation."""
    await websocket.accept()
    await broker.handle_client(room_id, websocket)
