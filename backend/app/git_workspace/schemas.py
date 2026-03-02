from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class GitAuthMode(str, Enum):
    """Authentication strategy for git operations."""

    TOKEN = "token"       # Mode A – backend holds a PAT in memory
    DELEGATE = "delegate" # Mode B – client supplies credentials on demand


class WorktreeStatus(str, Enum):
    """Lifecycle state of a git worktree."""

    PENDING   = "pending"    # created, but clone/checkout not yet complete
    READY     = "ready"      # worktree is fully checked out
    SYNCING   = "syncing"    # an operation (push/pull) is in flight
    ERROR     = "error"      # last operation failed
    DESTROYED = "destroyed"  # teardown complete, path removed


# ---------------------------------------------------------------------------
# Credential payloads  (transmitted over WebSocket, never persisted)
# ---------------------------------------------------------------------------


class CredentialPayload(BaseModel):
    """Credentials supplied by the host client (Mode A)."""

    token: str = Field(..., description="Personal Access Token or OAuth token")
    username: Optional[str] = Field(
        default=None,
        description="Git username.  Defaults to ‘git’ for most providers.",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Hard expiry hint from client.  Backend enforces its own TTL regardless.",
    )


class DelegateAuthRequest(BaseModel):
    """Sent by backend → client when a git operation needs credentials (Mode B)."""

    request_id: str = Field(..., description="Opaque ID used to correlate request/response.")
    repo_url:   str = Field(..., description="Remote URL that triggered the auth challenge.")
    operation:  str = Field(..., description="git verb, e.g. ‘clone’, ‘fetch’, ‘push’.")


class DelegateAuthResponse(BaseModel):
    """Sent by client → backend in reply to *DelegateAuthRequest* (Mode B)."""

    request_id: str
    token:      str
    username:   Optional[str] = None


# ---------------------------------------------------------------------------
# Workspace / Worktree models
# ---------------------------------------------------------------------------


class WorkspaceCreateRequest(BaseModel):
    """Payload to create a new git-backed workspace for a room."""

    room_id:     str  = Field(..., description="Unique room identifier.")
    repo_url:    str  = Field(..., description="Remote git repository URL.")
    base_branch: str  = Field(default="main", description="Branch to base the worktree on.")
    credentials: Optional[CredentialPayload] = Field(
        default=None,
        description="Required when auth_mode=token; omitted for delegate mode.",
    )


class WorkspaceInfo(BaseModel):
    """Public state of a workspace, safe to return over the API."""

    room_id:      str
    repo_url:     str
    branch:       str
    worktree_path: str
    status:       WorktreeStatus
    created_at:   datetime
    last_synced:  Optional[datetime] = None
    error_detail: Optional[str]      = None


class WorkspaceSyncRequest(BaseModel):
    """Ask the backend to pull the latest remote changes into the worktree."""

    room_id:   str
    rebase:    bool = Field(default=False, description="Use rebase instead of merge.")


class WorkspaceSyncResult(BaseModel):
    room_id:   str
    success:   bool
    message:   str
    conflicts: List[str] = Field(default_factory=list)


class WorkspaceCommitRequest(BaseModel):
    """Stage all changes and create a commit in the worktree branch."""

    room_id: str
    message: str = Field(..., description="Commit message.")
    author_name:  Optional[str] = None
    author_email: Optional[str] = None


class WorkspaceCommitResult(BaseModel):
    room_id: str
    success: bool
    sha:     Optional[str] = None   # commit SHA on success
    message: str


class WorkspacePushRequest(BaseModel):
    """Push the worktree branch to the remote."""

    room_id:     str
    force:       bool = Field(default=False, description="Force-push (use with caution).")


class WorkspacePushResult(BaseModel):
    room_id:     str
    success:     bool
    remote_url:  Optional[str] = None
    pushed_sha:  Optional[str] = None
    message:     str


class WorkspaceDestroyResult(BaseModel):
    room_id: str
    success: bool
    message: str


# ---------------------------------------------------------------------------
# File-sync broadcast  (over WebSocket, not REST)
# ---------------------------------------------------------------------------


class FileSyncEvent(BaseModel):
    """Emitted by the backend to all room participants when the worktree changes."""

    event:     str              = "file_sync"
    room_id:   str
    changeset: List[FileChange]
    sync_id:   str              = Field(..., description="Monotonically increasing counter.")


class FileChange(BaseModel):
    """A single file modification within a FileSyncEvent."""

    path:      str
    operation: str   # "added" | "modified" | "deleted"
    content:   Optional[str] = None   # None for deletions


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class GitWorkspaceHealth(BaseModel):
    status:       str   # "ok" | "degraded" | "error"
    active_rooms: int
    git_version:  str
    detail:       Optional[str] = None
