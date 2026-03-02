"""Git Workspace Service — core operations.

Manages bare clones + git worktrees on the local filesystem, one worktree
per chat room.  Supports Mode A (token/GIT_ASKPASS) and Mode B (delegate)
authentication.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .credential_store import CredentialStore
from .schemas import (
    CredentialPayload,
    FileSyncEvent,
    FileChange,
    WorkspaceCommitRequest,
    WorkspaceCommitResult,
    WorkspaceCreateRequest,
    WorkspaceDestroyResult,
    WorkspaceInfo,
    WorkspacePushRequest,
    WorkspacePushResult,
    WorkspaceSyncRequest,
    WorkspaceSyncResult,
    WorktreeStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


class _WorktreeRecord:
    """In-process record for a single room's worktree."""

    __slots__ = (
        "room_id", "repo_url", "branch", "worktree_path",
        "status", "created_at", "last_synced", "error_detail",
    )

    def __init__(
        self,
        room_id: str,
        repo_url: str,
        branch: str,
        worktree_path: Path,
    ) -> None:
        self.room_id       = room_id
        self.repo_url      = repo_url
        self.branch        = branch
        self.worktree_path = worktree_path
        self.status        = WorktreeStatus.PENDING
        self.created_at    = datetime.now(timezone.utc)
        self.last_synced:  Optional[datetime] = None
        self.error_detail: Optional[str]      = None

    def to_info(self) -> WorkspaceInfo:
        return WorkspaceInfo(
            room_id       = self.room_id,
            repo_url      = self.repo_url,
            branch        = self.branch,
            worktree_path = str(self.worktree_path),
            status        = self.status,
            created_at    = self.created_at,
            last_synced   = self.last_synced,
            error_detail  = self.error_detail,
        )


# ---------------------------------------------------------------------------
# Helper – GIT_ASKPASS script
# ---------------------------------------------------------------------------

_ASKPASS_SCRIPT = """\
#!/bin/sh
# Minimal GIT_ASKPASS helper.  Reads credentials from env vars set by the
# parent process before spawning git.  Never writes credentials to stdout
# unless queried.
case "$1" in
  *Username*) echo "${GIT_CREDENTIAL_USERNAME}" ;;
  *Password*) echo "${GIT_CREDENTIAL_TOKEN}"    ;;
esac
"""


def _make_askpass_script() -> str:
    """Write the GIT_ASKPASS helper to a temp file and return its path."""
    fd, path = tempfile.mkstemp(prefix="conductor_askpass_", suffix=".sh")
    try:
        os.write(fd, _ASKPASS_SCRIPT.encode())
    finally:
        os.close(fd)
    os.chmod(path, 0o700)
    return path


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class GitWorkspaceService:
    """
    Manages the full lifecycle of git-backed workspaces:

      * clone (bare) a remote repo once per URL
      * create / tear-down git worktrees per room
      * perform authenticated git operations (fetch / push)
      * broadcast file-change events to room WebSocket connections
    """

    def __init__(self) -> None:
        self._workspaces_dir: Path = Path("./workspaces")
        self._worktrees: Dict[str, _WorktreeRecord] = {}
        self._credential_store = CredentialStore()
        self._broadcast_callbacks: Dict[str, list] = {}  # room_id → [callbacks]
        self._max_worktrees: int = 20
        self._cleanup_on_close: bool = True
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, settings) -> None:  # settings: GitWorkspaceSettings
        """Call once from app lifespan on startup."""
        self._workspaces_dir  = Path(settings.workspaces_dir)
        self._max_worktrees   = settings.max_worktrees_per_repo
        self._cleanup_on_close = settings.cleanup_on_room_close
        self._workspaces_dir.mkdir(parents=True, exist_ok=True)
        await self._credential_store.start()
        self._initialized = True
        logger.info(
            "GitWorkspaceService initialized (dir=%s, auth_mode=%s)",
            self._workspaces_dir,
            settings.git_auth_mode,
        )

    async def shutdown(self) -> None:
        """Graceful shutdown — wipe credentials."""
        await self._credential_store.stop()
        self._initialized = False
        logger.info("GitWorkspaceService shut down.")

    # ------------------------------------------------------------------
    # Credential management
    # ------------------------------------------------------------------

    async def store_credentials(
        self,
        room_id: str,
        payload: CredentialPayload,
    ) -> None:
        await self._credential_store.put(room_id, payload)

    async def revoke_credentials(self, room_id: str) -> None:
        await self._credential_store.delete(room_id)

    # ------------------------------------------------------------------
    # Workspace creation
    # ------------------------------------------------------------------

    async def create_workspace(self, req: WorkspaceCreateRequest) -> WorkspaceInfo:
        if len(self._worktrees) >= self._max_worktrees:
            raise RuntimeError(
                f"Maximum concurrent worktrees ({self._max_worktrees}) reached."
            )
        if req.room_id in self._worktrees:
            return self._worktrees[req.room_id].to_info()

        # Store credentials if supplied (Mode A)
        if req.credentials:
            await self._credential_store.put(req.room_id, req.credentials)

        repo_hash    = hashlib.sha256(req.repo_url.encode()).hexdigest()[:12]
        repo_dir     = self._workspaces_dir / repo_hash
        bare_dir     = repo_dir / "bare.git"
        worktrees_dir = repo_dir / "worktrees"
        worktree_path = worktrees_dir / req.room_id
        branch        = f"session/{req.room_id}"

        record = _WorktreeRecord(
            room_id       = req.room_id,
            repo_url      = req.repo_url,
            branch        = branch,
            worktree_path = worktree_path,
        )
        self._worktrees[req.room_id] = record

        asyncio.create_task(
            self._setup_worktree(
                record      = record,
                req         = req,
                bare_dir    = bare_dir,
                worktrees_dir = worktrees_dir,
            )
        )
        return record.to_info()

    async def _setup_worktree(
        self,
        record:        _WorktreeRecord,
        req:           WorkspaceCreateRequest,
        bare_dir:      Path,
        worktrees_dir: Path,
    ) -> None:
        """Background task: clone bare repo (if needed) then create worktree."""
        try:
            env = await self._build_git_env(req.room_id)
            worktrees_dir.mkdir(parents=True, exist_ok=True)

            # --- 1. Bare clone (idempotent) ---
            if not bare_dir.exists():
                logger.info("Cloning %s → %s (bare)", req.repo_url, bare_dir)
                record.status = WorktreeStatus.SYNCING
                await self._run_git(
                    ["clone", "--bare", req.repo_url, str(bare_dir)],
                    env=env,
                )
            else:
                logger.debug("Bare repo already exists at %s", bare_dir)

            # --- 2. Create the worktree on a new branch ---
            logger.info("Creating worktree for room %s (branch=%s)", req.room_id, record.branch)
            await self._run_git(
                [
                    "-C", str(bare_dir),
                    "worktree", "add",
                    "-b", record.branch,
                    str(record.worktree_path),
                    req.base_branch,
                ],
                env=env,
            )

            record.status      = WorktreeStatus.READY
            record.last_synced = datetime.now(timezone.utc)
            logger.info("Worktree ready for room %s at %s", req.room_id, record.worktree_path)

        except Exception as exc:  # pylint: disable=broad-except
            record.status       = WorktreeStatus.ERROR
            record.error_detail = str(exc)
            logger.error("Failed to set up worktree for room %s: %s", req.room_id, exc)

    # ------------------------------------------------------------------
    # Sync (pull)
    # ------------------------------------------------------------------

    async def sync_workspace(self, req: WorkspaceSyncRequest) -> WorkspaceSyncResult:
        record = self._get_record(req.room_id)
        try:
            record.status = WorktreeStatus.SYNCING
            env  = await self._build_git_env(req.room_id)
            verb = ["rebase"] if req.rebase else ["pull"]
            await self._run_git(["--work-tree", str(record.worktree_path)] + verb, cwd=record.worktree_path, env=env)
            record.status      = WorktreeStatus.READY
            record.last_synced = datetime.now(timezone.utc)
            return WorkspaceSyncResult(room_id=req.room_id, success=True, message="Sync complete")
        except Exception as exc:
            record.status       = WorktreeStatus.ERROR
            record.error_detail = str(exc)
            return WorkspaceSyncResult(
                room_id=req.room_id, success=False, message=str(exc)
            )

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------

    async def commit_workspace(
        self, req: WorkspaceCommitRequest
    ) -> WorkspaceCommitResult:
        record = self._get_record(req.room_id)
        try:
            env = await self._build_git_env(req.room_id)
            cwd = record.worktree_path

            # Stage all changes
            await self._run_git(["add", "-A"], cwd=cwd, env=env)

            # Build commit command
            git_cmd = ["commit", "-m", req.message]
            if req.author_name and req.author_email:
                git_cmd += [
                    f"--author={req.author_name} <{req.author_email}>"
                ]
            await self._run_git(git_cmd, cwd=cwd, env=env)

            # Retrieve SHA
            sha = await self._get_head_sha(cwd, env)
            return WorkspaceCommitResult(
                room_id=req.room_id, success=True, sha=sha, message="Commit created"
            )
        except Exception as exc:
            return WorkspaceCommitResult(
                room_id=req.room_id, success=False, message=str(exc)
            )

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    async def push_workspace(self, req: WorkspacePushRequest) -> WorkspacePushResult:
        record = self._get_record(req.room_id)
        try:
            env  = await self._build_git_env(req.room_id)
            cwd  = record.worktree_path
            args = ["push", "origin", record.branch]
            if req.force:
                args.append("--force")
            await self._run_git(args, cwd=cwd, env=env)
            sha = await self._get_head_sha(cwd, env)
            return WorkspacePushResult(
                room_id=req.room_id,
                success=True,
                remote_url=record.repo_url,
                pushed_sha=sha,
                message="Push successful",
            )
        except Exception as exc:
            return WorkspacePushResult(
                room_id=req.room_id, success=False, message=str(exc)
            )

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    async def destroy_workspace(self, room_id: str) -> WorkspaceDestroyResult:
        record = self._worktrees.pop(room_id, None)
        if record is None:
            return WorkspaceDestroyResult(
                room_id=room_id, success=False, message="Workspace not found"
            )
        await self._credential_store.delete(room_id)
        if self._cleanup_on_close and record.worktree_path.exists():
            try:
                shutil.rmtree(record.worktree_path)
                logger.info("Worktree directory removed: %s", record.worktree_path)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Could not remove worktree dir: %s", exc)
        record.status = WorktreeStatus.DESTROYED
        return WorkspaceDestroyResult(
            room_id=room_id, success=True, message="Workspace destroyed"
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_worktree_path(self, room_id: str) -> Optional[Path]:
        record = self._worktrees.get(room_id)
        return record.worktree_path if record else None

    def list_workspaces(self) -> List[WorkspaceInfo]:
        return [r.to_info() for r in self._worktrees.values()]

    def get_workspace(self, room_id: str) -> Optional[WorkspaceInfo]:
        record = self._worktrees.get(room_id)
        return record.to_info() if record else None

    # ------------------------------------------------------------------
    # WebSocket file-sync broadcast
    # ------------------------------------------------------------------

    def register_broadcast(
        self, room_id: str, callback  # Callable[[FileSyncEvent], Awaitable[None]]
    ) -> None:
        self._broadcast_callbacks.setdefault(room_id, []).append(callback)

    def unregister_broadcast(self, room_id: str, callback) -> None:
        cbs = self._broadcast_callbacks.get(room_id, [])
        try:
            cbs.remove(callback)
        except ValueError:
            pass

    async def broadcast_file_sync(
        self, room_id: str, changes: List[FileChange], sync_id: str
    ) -> None:
        event = FileSyncEvent(
            room_id=room_id,
            changeset=changes,
            sync_id=sync_id,
        )
        for cb in list(self._broadcast_callbacks.get(room_id, [])):
            try:
                await cb(event)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Broadcast callback error for room %s: %s", room_id, exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_record(self, room_id: str) -> _WorktreeRecord:
        record = self._worktrees.get(room_id)
        if record is None:
            raise KeyError(f"No workspace found for room_id={room_id!r}")
        return record

    async def _build_git_env(
        self, room_id: str
    ) -> Dict[str, str]:
        """Build an env-var dict for git that injects credentials if available."""
        base_env = os.environ.copy()
        creds    = await self._credential_store.get(room_id)
        if creds is None:
            return base_env  # delegate mode – no stored creds

        askpass_path = _make_askpass_script()
        base_env.update(
            {
                "GIT_ASKPASS":            askpass_path,
                "GIT_CREDENTIAL_USERNAME": creds.username or "git",
                "GIT_CREDENTIAL_TOKEN":   creds.token,
                "GIT_TERMINAL_PROMPT":    "0",
            }
        )
        return base_env

    @staticmethod
    async def _run_git(
        args: List[str],
        cwd:  Optional[Path] = None,
        env:  Optional[Dict[str, str]] = None,
    ) -> str:
        """Run a git sub-command asynchronously; return stdout."""
        cmd = ["git"] + args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode(errors="replace").strip()
        stderr = stderr_b.decode(errors="replace").strip()
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {args[0]} failed (exit {proc.returncode}): {stderr or stdout}"
            )
        return stdout

    async def _get_head_sha(self, cwd: Path, env: Dict[str, str]) -> Optional[str]:
        try:
            return await self._run_git(["rev-parse", "HEAD"], cwd=cwd, env=env)
        except Exception:  # pylint: disable=broad-except
            return None
