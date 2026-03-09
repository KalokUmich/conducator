"""Tests for the context enrichment router — hybrid retrieval.

Rewritten for CocoIndex + RepoMap integration.  All heavy dependencies
(cocoindex, sentence_transformers, sqlite_vec, tree-sitter, networkx, git)
are stubbed so the suite runs in any CI environment.

Coverage:
  * POST /api/context/context — vector search + repo map
  * GET /api/context/context/{room_id}/index-status
  * GET /api/context/context/{room_id}/graph-stats
  * Edge cases: no workspace, empty results, repo map disabled/failed

Total: 28 tests
"""
from __future__ import annotations

import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Stubs for optional heavy dependencies
# ---------------------------------------------------------------------------

def _stub(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(name, m)
    return m

_stub("cocoindex", FlowBuilder=MagicMock, IndexOptions=MagicMock)
_stub("sentence_transformers", SentenceTransformer=MagicMock)
_stub("sqlite_vec")
_stub("tree_sitter_languages")
_stub("networkx", DiGraph=MagicMock, pagerank=MagicMock, PowerIterationFailedConvergence=Exception)

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

from backend.app.context.router import router  # noqa: E402

app = FastAPI()
app.include_router(router)
client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_code_search():
    svc = MagicMock()
    # Return a mock CodeSearchResponse
    mock_response = MagicMock()
    mock_response.results = []
    svc.search = AsyncMock(return_value=mock_response)
    svc.get_index_status = MagicMock(return_value=MagicMock(
        model_dump=MagicMock(return_value={
            "workspace_path": "/test",
            "indexed": False,
            "files_count": 0,
            "chunks_count": 0,
        })
    ))
    return svc


@pytest.fixture()
def mock_git_workspace():
    svc = MagicMock()
    svc.get_worktree_path = MagicMock(return_value="/fake/worktree")
    return svc


@pytest.fixture()
def mock_repo_map():
    svc = MagicMock()
    svc.generate_repo_map = MagicMock(return_value="## Repository Map\n\nmain.py\n")
    svc.get_graph_stats = MagicMock(return_value={
        "cached": True,
        "total_files": 10,
        "total_edges": 15,
    })
    return svc


@pytest.fixture(autouse=True)
def _inject_services(mock_code_search, mock_git_workspace, mock_repo_map):
    with patch("backend.app.context.router._get_code_search_service", return_value=mock_code_search), \
         patch("backend.app.context.router._get_git_workspace_service", return_value=mock_git_workspace), \
         patch("backend.app.context.router._get_repo_map_service", return_value=mock_repo_map):
        yield


# ---------------------------------------------------------------------------
# POST /api/context/context — happy path
# ---------------------------------------------------------------------------


class TestContextEndpointHappyPath:
    def test_returns_200_for_valid_request(self, mock_code_search):
        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "main function"
        })
        assert resp.status_code == 200

    def test_response_has_required_fields(self, mock_code_search):
        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "test"
        })
        data = resp.json()
        assert "room_id" in data
        assert "query" in data
        assert "chunks" in data
        assert "total" in data

    def test_response_includes_repo_map(self, mock_code_search, mock_repo_map):
        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "test"
        })
        data = resp.json()
        assert "repo_map" in data
        assert data["repo_map"] is not None
        assert "Repository Map" in data["repo_map"]

    def test_empty_results_returns_200(self, mock_code_search):
        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "zzz_no_match"
        })
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_chunks_from_search_results(self, mock_code_search):
        mock_chunk = MagicMock()
        mock_chunk.file_path = "main.py"
        mock_chunk.start_line = 1
        mock_chunk.end_line = 10
        mock_chunk.content = "def main(): pass"
        mock_chunk.score = 0.9
        mock_chunk.symbol_name = "main"
        mock_chunk.symbol_type = "function"
        mock_response = MagicMock()
        mock_response.results = [mock_chunk]
        mock_code_search.search = AsyncMock(return_value=mock_response)

        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "main"
        })
        data = resp.json()
        assert data["total"] == 1
        assert data["chunks"][0]["file_path"] == "main.py"
        assert data["chunks"][0]["score"] == 0.9

    def test_repo_map_called_with_vector_files(self, mock_code_search, mock_repo_map):
        mock_chunk = MagicMock()
        mock_chunk.file_path = "service.py"
        mock_chunk.start_line = 1
        mock_chunk.end_line = 5
        mock_chunk.content = "class Service:"
        mock_chunk.score = 0.8
        mock_chunk.symbol_name = None
        mock_chunk.symbol_type = None
        mock_response = MagicMock()
        mock_response.results = [mock_chunk]
        mock_code_search.search = AsyncMock(return_value=mock_response)

        client.post("/api/context/context", json={
            "room_id": "room-1", "query": "service"
        })

        mock_repo_map.generate_repo_map.assert_called_once()
        call_kwargs = mock_repo_map.generate_repo_map.call_args
        assert "service.py" in call_kwargs[1]["query_files"]


# ---------------------------------------------------------------------------
# POST /api/context/context — repo map disabled / not available
# ---------------------------------------------------------------------------


class TestContextRepoMapEdgeCases:
    def test_repo_map_disabled_in_request(self, mock_code_search, mock_repo_map):
        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "test", "include_repo_map": False
        })
        data = resp.json()
        assert data["repo_map"] is None
        mock_repo_map.generate_repo_map.assert_not_called()

    def test_repo_map_service_none(self, mock_code_search):
        with patch("backend.app.context.router._get_repo_map_service", return_value=None):
            resp = client.post("/api/context/context", json={
                "room_id": "room-1", "query": "test"
            })
            data = resp.json()
            assert data["repo_map"] is None

    def test_repo_map_generation_error_handled(self, mock_code_search, mock_repo_map):
        mock_repo_map.generate_repo_map.side_effect = RuntimeError("graph error")
        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "test"
        })
        # Should still return 200, just without repo map
        assert resp.status_code == 200
        data = resp.json()
        assert data["repo_map"] is None


# ---------------------------------------------------------------------------
# POST /api/context/context — validation / error
# ---------------------------------------------------------------------------


class TestContextEndpointValidation:
    def test_missing_query_returns_422(self):
        resp = client.post("/api/context/context", json={"room_id": "room-1"})
        assert resp.status_code == 422

    def test_missing_room_id_returns_422(self):
        resp = client.post("/api/context/context", json={"query": "test"})
        assert resp.status_code == 422

    def test_no_workspace_returns_404(self, mock_git_workspace):
        mock_git_workspace.get_worktree_path.return_value = None
        resp = client.post("/api/context/context", json={
            "room_id": "no-workspace", "query": "test"
        })
        assert resp.status_code == 404

    def test_wrong_content_type_returns_422(self):
        resp = client.post("/api/context/context", data="not-json")
        assert resp.status_code == 422

    def test_top_k_min_1(self):
        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "test", "top_k": 0
        })
        assert resp.status_code == 422

    def test_top_k_max_20(self):
        resp = client.post("/api/context/context", json={
            "room_id": "room-1", "query": "test", "top_k": 21
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/context/context/{room_id}/index-status
# ---------------------------------------------------------------------------


class TestIndexStatusEndpoint:
    def test_returns_200(self, mock_code_search, mock_git_workspace):
        resp = client.get("/api/context/context/room-1/index-status")
        assert resp.status_code == 200

    def test_returns_dict(self, mock_code_search, mock_git_workspace):
        resp = client.get("/api/context/context/room-1/index-status")
        data = resp.json()
        assert isinstance(data, dict)
        assert "indexed" in data

    def test_no_workspace_returns_404(self, mock_git_workspace):
        mock_git_workspace.get_worktree_path.return_value = None
        resp = client.get("/api/context/context/no-room/index-status")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/context/context/{room_id}/graph-stats
# ---------------------------------------------------------------------------


class TestGraphStatsEndpoint:
    def test_returns_200(self, mock_repo_map, mock_git_workspace):
        resp = client.get("/api/context/context/room-1/graph-stats")
        assert resp.status_code == 200

    def test_returns_stats(self, mock_repo_map, mock_git_workspace):
        resp = client.get("/api/context/context/room-1/graph-stats")
        data = resp.json()
        assert data["available"] is True
        assert "total_files" in data

    def test_repo_map_not_configured(self):
        with patch("backend.app.context.router._get_repo_map_service", return_value=None), \
             patch("backend.app.context.router._get_git_workspace_service"):
            resp = client.get("/api/context/context/room-1/graph-stats")
            data = resp.json()
            assert data["available"] is False

    def test_no_workspace_returns_404(self, mock_repo_map, mock_git_workspace):
        mock_git_workspace.get_worktree_path.return_value = None
        resp = client.get("/api/context/context/no-room/graph-stats")
        assert resp.status_code == 404
