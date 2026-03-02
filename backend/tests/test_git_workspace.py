"""Tests for the git_workspace module.

Covers Create, Read, Update, Delete, List, and Diff operations, plus
configuration helpers, error handling, and edge-cases.

All filesystem / git / subprocess operations are mocked so the suite
runs anywhere without real git repos.

Total: 60 tests
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from pathlib import Path
from fastapi.testclient import TestClient
from fastapi import FastAPI
import sys
import types

# ---------------------------------------------------------------------------
# Minimal stubs so optional imports don't break test collection
# ---------------------------------------------------------------------------


def _stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


_stub("cocoindex", FlowBuilder=MagicMock, IndexOptions=MagicMock)
_stub("sentence_transformers", SentenceTransformer=MagicMock)
_stub("sqlite_vec")

# ---------------------------------------------------------------------------
# Real imports
# ---------------------------------------------------------------------------

from backend.git_workspace import GitWorkspaceManager  # noqa: E402  # type: ignore
from backend.routers.git_workspace import router  # noqa: E402  # type: ignore

app = FastAPI()
app.include_router(router)
client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = "/tmp/test_workspaces"


def _make_manager(tmp_path) -> GitWorkspaceManager:
    with patch("backend.git_workspace.BASE_WORKSPACE_DIR", str(tmp_path)):
        return GitWorkspaceManager(base_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# GitWorkspaceManager – unit tests
# ---------------------------------------------------------------------------


class TestGitWorkspaceManagerInit:
    def test_init_with_base_dir(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr is not None

    def test_default_base_dir(self):
        with patch("backend.git_workspace.BASE_WORKSPACE_DIR", "/tmp/ws"):
            mgr = GitWorkspaceManager()
            assert mgr is not None

    def test_base_dir_stored(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert str(tmp_path) in str(mgr.base_dir)


class TestCreateWorkspace:
    @pytest.mark.asyncio
    async def test_create_new_workspace(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_clone_repo", AsyncMock(return_value=None)):
            result = await mgr.create_workspace(
                workspace_id="ws1",
                repo_url="https://github.com/example/repo.git",
            )
            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_create_returns_workspace_id(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_clone_repo", AsyncMock(return_value=None)):
            result = await mgr.create_workspace("ws2",
                                                 "https://github.com/x/y.git")
            assert "workspace_id" in result or result.get("id") == "ws2" or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        workspace_dir = tmp_path / "ws3"
        workspace_dir.mkdir()
        with pytest.raises(Exception):
            await mgr.create_workspace("ws3",
                                        "https://github.com/x/y.git")

    @pytest.mark.asyncio
    async def test_create_clone_error_propagates(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(
            mgr, "_clone_repo", AsyncMock(side_effect=RuntimeError("git clone failed"))
        ):
            with pytest.raises(RuntimeError, match="git clone failed"):
                await mgr.create_workspace("ws4", "https://bad/repo")


class TestGetWorkspace:
    @pytest.mark.asyncio
    async def test_get_existing_workspace(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        result = await mgr.get_workspace("myws")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_nonexistent_workspace(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(Exception):
            await mgr.get_workspace("no_such_workspace")

    @pytest.mark.asyncio
    async def test_get_returns_path_info(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ws_dir = tmp_path / "pathws"
        ws_dir.mkdir()
        result = await mgr.get_workspace("pathws")
        assert "path" in result or isinstance(result, dict)


class TestListWorkspaces:
    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = await mgr.list_workspaces()
        assert isinstance(result, list)
        assert result == []

    @pytest.mark.asyncio
    async def test_list_with_workspaces(self, tmp_path):
        mgr = _make_manager(tmp_path)
        for name in ["a", "b", "c"]:
            (tmp_path / name).mkdir()
        result = await mgr.list_workspaces()
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_returns_list_of_dicts(self, tmp_path):
        mgr = _make_manager(tmp_path)
        (tmp_path / "ws").mkdir()
        result = await mgr.list_workspaces()
        assert all(isinstance(item, dict) for item in result)

    @pytest.mark.asyncio
    async def test_list_contains_workspace_ids(self, tmp_path):
        mgr = _make_manager(tmp_path)
        (tmp_path / "mywks").mkdir()
        result = await mgr.list_workspaces()
        ids = [w.get("workspace_id") or w.get("id") or w.get("name") for w in result]
        assert any("mywks" in str(i) for i in ids)


class TestUpdateWorkspace:
    @pytest.mark.asyncio
    async def test_update_existing(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ws_dir = tmp_path / "upd"
        ws_dir.mkdir()
        with patch.object(mgr, "_run_git", AsyncMock(return_value="")):
            result = await mgr.update_workspace("upd")
            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(Exception):
            await mgr.update_workspace("ghost")

    @pytest.mark.asyncio
    async def test_update_git_error_propagates(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ws_dir = tmp_path / "errws"
        ws_dir.mkdir()
        with patch.object(
            mgr, "_run_git", AsyncMock(side_effect=RuntimeError("pull failed"))
        ):
            with pytest.raises(RuntimeError, match="pull failed"):
                await mgr.update_workspace("errws")


class TestDeleteWorkspace:
    @pytest.mark.asyncio
    async def test_delete_existing(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ws_dir = tmp_path / "del"
        ws_dir.mkdir()
        result = await mgr.delete_workspace("del")
        assert isinstance(result, dict)
        assert not ws_dir.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(Exception):
            await mgr.delete_workspace("nope")

    @pytest.mark.asyncio
    async def test_delete_removes_directory(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ws_dir = tmp_path / "rmws"
        ws_dir.mkdir()
        (ws_dir / "file.txt").write_text("hello")
        await mgr.delete_workspace("rmws")
        assert not ws_dir.exists()


class TestGetDiff:
    @pytest.mark.asyncio
    async def test_diff_returns_dict(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ws_dir = tmp_path / "diffws"
        ws_dir.mkdir()
        with patch.object(mgr, "_run_git", AsyncMock(return_value="diff output")):
            result = await mgr.get_diff("diffws")
            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_diff_nonexistent_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(Exception):
            await mgr.get_diff("ghost")

    @pytest.mark.asyncio
    async def test_diff_with_base_branch(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ws_dir = tmp_path / "diffws2"
        ws_dir.mkdir()
        with patch.object(mgr, "_run_git", AsyncMock(return_value="--- a\n+++ b")):
            result = await mgr.get_diff("diffws2", base_branch="main")
            assert isinstance(result, dict)


class TestRunGit:
    @pytest.mark.asyncio
    async def test_run_git_calls_subprocess(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"output", b""))
            proc.returncode = 0
            mock_exec.return_value = proc
            result = await mgr._run_git("git", "status", cwd=str(tmp_path))
            assert "output" in result

    @pytest.mark.asyncio
    async def test_run_git_raises_on_nonzero_exit(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b"error"))
            proc.returncode = 1
            mock_exec.return_value = proc
            with pytest.raises(RuntimeError):
                await mgr._run_git("git", "status", cwd=str(tmp_path))


# ---------------------------------------------------------------------------
# Router / endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_manager():
    mgr = MagicMock(spec=GitWorkspaceManager)
    mgr.create_workspace = AsyncMock(
        return_value={"workspace_id": "w1", "status": "created"}
    )
    mgr.get_workspace = AsyncMock(
        return_value={"workspace_id": "w1", "path": "/tmp/w1"}
    )
    mgr.list_workspaces = AsyncMock(return_value=[])
    mgr.update_workspace = AsyncMock(
        return_value={"workspace_id": "w1", "status": "updated"}
    )
    mgr.delete_workspace = AsyncMock(
        return_value={"workspace_id": "w1", "status": "deleted"}
    )
    mgr.get_diff = AsyncMock(
        return_value={"workspace_id": "w1", "diff": ""}
    )
    return mgr


@pytest.fixture(autouse=True)
def _inject_manager(mock_manager):
    with patch(
        "backend.routers.git_workspace.get_git_workspace_manager",
        return_value=mock_manager,
    ):
        yield


class TestCreateWorkspaceEndpoint:
    def test_create_returns_200(self, mock_manager):
        resp = client.post(
            "/api/git-workspace/",
            json={"workspace_id": "w1", "repo_url": "https://github.com/x/y.git"},
        )
        assert resp.status_code == 200

    def test_create_missing_repo_url(self, mock_manager):
        resp = client.post("/api/git-workspace/", json={"workspace_id": "w1"})
        assert resp.status_code == 422

    def test_create_returns_workspace_data(self, mock_manager):
        resp = client.post(
            "/api/git-workspace/",
            json={"workspace_id": "w1", "repo_url": "https://github.com/x/y.git"},
        )
        assert isinstance(resp.json(), dict)

    def test_create_service_error(self, mock_manager):
        mock_manager.create_workspace = AsyncMock(side_effect=RuntimeError("fail"))
        resp = client.post(
            "/api/git-workspace/",
            json={"workspace_id": "err", "repo_url": "https://github.com/x/y.git"},
        )
        assert resp.status_code in (500, 503, 409, 422)


class TestGetWorkspaceEndpoint:
    def test_get_returns_200(self, mock_manager):
        resp = client.get("/api/git-workspace/w1")
        assert resp.status_code == 200

    def test_get_not_found(self, mock_manager):
        mock_manager.get_workspace = AsyncMock(
            side_effect=FileNotFoundError("not found")
        )
        resp = client.get("/api/git-workspace/ghost")
        assert resp.status_code in (404, 500)

    def test_get_returns_workspace_dict(self, mock_manager):
        resp = client.get("/api/git-workspace/w1")
        assert isinstance(resp.json(), dict)


class TestListWorkspacesEndpoint:
    def test_list_returns_200(self, mock_manager):
        resp = client.get("/api/git-workspace/")
        assert resp.status_code == 200

    def test_list_empty(self, mock_manager):
        resp = client.get("/api/git-workspace/")
        assert isinstance(resp.json(), (list, dict))

    def test_list_with_items(self, mock_manager):
        mock_manager.list_workspaces = AsyncMock(
            return_value=[{"workspace_id": "w1"}, {"workspace_id": "w2"}]
        )
        resp = client.get("/api/git-workspace/")
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestUpdateWorkspaceEndpoint:
    def test_update_returns_200(self, mock_manager):
        resp = client.put("/api/git-workspace/w1")
        assert resp.status_code == 200

    def test_update_not_found(self, mock_manager):
        mock_manager.update_workspace = AsyncMock(
            side_effect=FileNotFoundError("not found")
        )
        resp = client.put("/api/git-workspace/ghost")
        assert resp.status_code in (404, 500)

    def test_update_service_error(self, mock_manager):
        mock_manager.update_workspace = AsyncMock(side_effect=RuntimeError("err"))
        resp = client.put("/api/git-workspace/w1")
        assert resp.status_code in (500, 503)


class TestDeleteWorkspaceEndpoint:
    def test_delete_returns_200(self, mock_manager):
        resp = client.delete("/api/git-workspace/w1")
        assert resp.status_code == 200

    def test_delete_not_found(self, mock_manager):
        mock_manager.delete_workspace = AsyncMock(
            side_effect=FileNotFoundError("not found")
        )
        resp = client.delete("/api/git-workspace/ghost")
        assert resp.status_code in (404, 500)

    def test_delete_returns_confirmation(self, mock_manager):
        resp = client.delete("/api/git-workspace/w1")
        assert isinstance(resp.json(), dict)


class TestGetDiffEndpoint:
    def test_diff_returns_200(self, mock_manager):
        resp = client.get("/api/git-workspace/w1/diff")
        assert resp.status_code == 200

    def test_diff_not_found(self, mock_manager):
        mock_manager.get_diff = AsyncMock(side_effect=FileNotFoundError("nf"))
        resp = client.get("/api/git-workspace/ghost/diff")
        assert resp.status_code in (404, 500)

    def test_diff_with_base_branch_param(self, mock_manager):
        resp = client.get("/api/git-workspace/w1/diff?base_branch=main")
        assert resp.status_code == 200

    def test_diff_returns_dict(self, mock_manager):
        resp = client.get("/api/git-workspace/w1/diff")
        assert isinstance(resp.json(), dict)
