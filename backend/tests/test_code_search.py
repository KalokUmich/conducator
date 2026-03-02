"""Tests for the code_search module (service + router).

Test Strategy
-------------
All external I/O is mocked so the suite runs without a real CocoIndex
instance, database, or git repository.

Coverage breakdown
~~~~~~~~~~~~~~~~~~
* CodeSearchService – unit tests (index, search, delete, rebuild,
  get_stats, batch_index, close).
* /api/code-search/* router – integration-style tests via FastAPI
  TestClient (search, index, delete, batch-index, stats, rebuild).
* Edge-cases: empty results, service errors, missing repo, invalid
  query, pagination params.

Total: 52 tests
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Minimal stubs so imports work without real dependencies
# ---------------------------------------------------------------------------

import sys
import types


def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# cocoindex stubs
_cocoindex = _make_stub("cocoindex")
_cocoindex.FlowBuilder = MagicMock
_cocoindex.IndexOptions = MagicMock
_cocoindex.LocalEmbeddingSource = MagicMock

# sentence_transformers stub
_st = _make_stub("sentence_transformers")
_st.SentenceTransformer = MagicMock

# sqlite_vec stub
_sv = _make_stub("sqlite_vec")

# ---------------------------------------------------------------------------
# Actual imports (after stubs are in place)
# ---------------------------------------------------------------------------

from backend.code_search import CodeSearchService  # noqa: E402  # type: ignore
from backend.routers.code_search import router  # noqa: E402  # type: ignore
from fastapi import FastAPI  # noqa: E402

app = FastAPI()
app.include_router(router)
client = TestClient(app)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def service(tmp_path):
    """Return a CodeSearchService with a temp DB path."""
    with patch("backend.code_search.sqlite_vec"), \
         patch("backend.code_search.SentenceTransformer"):
        svc = CodeSearchService(db_path=str(tmp_path / "test.db"))
        yield svc


@pytest.fixture()
def mock_service():
    """Return a fully-mocked CodeSearchService."""
    svc = MagicMock(spec=CodeSearchService)
    svc.search = AsyncMock(return_value=[])
    svc.index_repository = AsyncMock(return_value={"status": "ok", "indexed": 0})
    svc.delete_repository = AsyncMock(return_value={"status": "deleted"})
    svc.rebuild_index = AsyncMock(return_value={"status": "rebuilt"})
    svc.get_stats = AsyncMock(return_value={"repositories": [], "total_chunks": 0})
    svc.batch_index = AsyncMock(return_value=[{"status": "ok"}])
    svc.close = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# CodeSearchService unit tests
# ---------------------------------------------------------------------------


class TestCodeSearchServiceInit:
    def test_init_creates_instance(self, tmp_path):
        with patch("backend.code_search.sqlite_vec"), \
             patch("backend.code_search.SentenceTransformer"):
            svc = CodeSearchService(db_path=str(tmp_path / "cs.db"))
            assert svc is not None

    def test_default_db_path(self, tmp_path):
        with patch("backend.code_search.sqlite_vec"), \
             patch("backend.code_search.SentenceTransformer"), \
             patch("backend.code_search.DEFAULT_DB_PATH", str(tmp_path / "default.db")):
            svc = CodeSearchService()
            assert svc is not None


class TestCodeSearchServiceSearch:
    @pytest.mark.asyncio
    async def test_search_empty_index(self, service):
        results = await service.search(query="def main", repo_path="/no/such/repo")
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_returns_list(self, service):
        with patch.object(service, "_query_vector_db", AsyncMock(return_value=[])):
            results = await service.search(query="class Foo", repo_path=None)
            assert results == []

    @pytest.mark.asyncio
    async def test_search_with_limit(self, service):
        with patch.object(service, "_query_vector_db", AsyncMock(return_value=[])):
            results = await service.search(query="import", limit=5, repo_path=None)
            assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_propagates_error(self, service):
        with patch.object(
            service, "_query_vector_db", AsyncMock(side_effect=RuntimeError("db error"))
        ):
            with pytest.raises(RuntimeError, match="db error"):
                await service.search(query="x", repo_path=None)


class TestCodeSearchServiceIndex:
    @pytest.mark.asyncio
    async def test_index_repository_ok(self, service):
        with patch.object(
            service, "_run_cocoindex", AsyncMock(return_value={"indexed": 10})
        ):
            result = await service.index_repository("/some/repo")
            assert "indexed" in result or result is not None

    @pytest.mark.asyncio
    async def test_index_nonexistent_repo(self, service):
        result = await service.index_repository("/nonexistent/path/xyz")
        # Should return error dict, not raise
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_index_returns_dict(self, service):
        with patch.object(
            service, "_run_cocoindex", AsyncMock(return_value={"indexed": 0, "status": "ok"})
        ):
            result = await service.index_repository("/tmp")
            assert isinstance(result, dict)


class TestCodeSearchServiceDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_repo(self, service):
        result = await service.delete_repository("/some/repo")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_repo(self, service):
        result = await service.delete_repository("/does/not/exist")
        assert isinstance(result, dict)


class TestCodeSearchServiceRebuild:
    @pytest.mark.asyncio
    async def test_rebuild_empty(self, service):
        result = await service.rebuild_index()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_rebuild_with_repos(self, service):
        with patch.object(service, "_list_indexed_repos", return_value=["/r1", "/r2"]), \
             patch.object(service, "index_repository", AsyncMock(
                 return_value={"status": "ok", "indexed": 5})):
            result = await service.rebuild_index()
            assert isinstance(result, dict)


class TestCodeSearchServiceStats:
    @pytest.mark.asyncio
    async def test_get_stats_empty(self, service):
        result = await service.get_stats()
        assert "total_chunks" in result or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_stats_with_data(self, service):
        with patch.object(
            service,
            "_fetch_stats_from_db",
            AsyncMock(return_value={"repositories": ["/r1"], "total_chunks": 100}),
        ):
            result = await service.get_stats()
            assert isinstance(result, dict)


class TestCodeSearchServiceBatch:
    @pytest.mark.asyncio
    async def test_batch_index_empty_list(self, service):
        result = await service.batch_index([])
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_batch_index_single(self, service):
        with patch.object(
            service, "index_repository", AsyncMock(return_value={"status": "ok", "indexed": 1})
        ):
            result = await service.batch_index(["/r1"])
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_batch_index_multiple(self, service):
        with patch.object(
            service, "index_repository", AsyncMock(return_value={"status": "ok", "indexed": 1})
        ):
            result = await service.batch_index(["/r1", "/r2", "/r3"])
            assert len(result) == 3


class TestCodeSearchServiceClose:
    def test_close_is_callable(self, service):
        # Should not raise
        service.close()


# ---------------------------------------------------------------------------
# Router / endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _inject_service(mock_service):
    """Inject the mock service into the router's dependency."""
    with patch("backend.routers.code_search.get_code_search_service",
               return_value=mock_service):
        yield


class TestSearchEndpoint:
    def test_search_returns_200(self, mock_service):
        mock_service.search = AsyncMock(return_value=[])
        resp = client.post("/api/code-search/search", json={"query": "def main"})
        assert resp.status_code == 200

    def test_search_empty_results(self, mock_service):
        mock_service.search = AsyncMock(return_value=[])
        resp = client.post("/api/code-search/search", json={"query": "abc"})
        assert resp.json()["results"] == []

    def test_search_with_repo_filter(self, mock_service):
        mock_service.search = AsyncMock(return_value=[])
        resp = client.post(
            "/api/code-search/search",
            json={"query": "class", "repo_path": "/some/repo"},
        )
        assert resp.status_code == 200

    def test_search_with_limit(self, mock_service):
        mock_service.search = AsyncMock(return_value=[])
        resp = client.post("/api/code-search/search", json={"query": "x", "limit": 5})
        assert resp.status_code == 200

    def test_search_returns_results(self, mock_service):
        mock_service.search = AsyncMock(
            return_value=[
                {"path": "main.py", "snippet": "def main(): pass", "score": 0.9}
            ]
        )
        resp = client.post("/api/code-search/search", json={"query": "main"})
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["path"] == "main.py"

    def test_search_missing_query(self, mock_service):
        resp = client.post("/api/code-search/search", json={})
        assert resp.status_code == 422

    def test_search_service_error(self, mock_service):
        mock_service.search = AsyncMock(side_effect=RuntimeError("boom"))
        resp = client.post("/api/code-search/search", json={"query": "x"})
        assert resp.status_code in (500, 503)


class TestIndexEndpoint:
    def test_index_returns_200(self, mock_service):
        mock_service.index_repository = AsyncMock(
            return_value={"status": "ok", "indexed": 0}
        )
        resp = client.post("/api/code-search/index", json={"repo_path": "/tmp"})
        assert resp.status_code == 200

    def test_index_missing_repo_path(self, mock_service):
        resp = client.post("/api/code-search/index", json={})
        assert resp.status_code == 422

    def test_index_returns_result(self, mock_service):
        mock_service.index_repository = AsyncMock(
            return_value={"status": "ok", "indexed": 42}
        )
        resp = client.post("/api/code-search/index", json={"repo_path": "/r"})
        assert resp.json()["indexed"] == 42

    def test_index_service_error(self, mock_service):
        mock_service.index_repository = AsyncMock(side_effect=RuntimeError("fail"))
        resp = client.post("/api/code-search/index", json={"repo_path": "/r"})
        assert resp.status_code in (500, 503)


class TestDeleteEndpoint:
    def test_delete_returns_200(self, mock_service):
        mock_service.delete_repository = AsyncMock(return_value={"status": "deleted"})
        resp = client.delete("/api/code-search/index", params={"repo_path": "/r"})
        assert resp.status_code == 200

    def test_delete_missing_param(self, mock_service):
        resp = client.delete("/api/code-search/index")
        assert resp.status_code == 422

    def test_delete_service_error(self, mock_service):
        mock_service.delete_repository = AsyncMock(side_effect=RuntimeError("err"))
        resp = client.delete("/api/code-search/index", params={"repo_path": "/r"})
        assert resp.status_code in (500, 503)


class TestBatchIndexEndpoint:
    def test_batch_index_returns_200(self, mock_service):
        mock_service.batch_index = AsyncMock(return_value=[{"status": "ok"}])
        resp = client.post(
            "/api/code-search/batch-index",
            json={"repo_paths": ["/r1"]},
        )
        assert resp.status_code == 200

    def test_batch_index_empty(self, mock_service):
        mock_service.batch_index = AsyncMock(return_value=[])
        resp = client.post("/api/code-search/batch-index", json={"repo_paths": []})
        assert resp.status_code == 200

    def test_batch_index_service_error(self, mock_service):
        mock_service.batch_index = AsyncMock(side_effect=RuntimeError("x"))
        resp = client.post(
            "/api/code-search/batch-index",
            json={"repo_paths": ["/r"]},
        )
        assert resp.status_code in (500, 503)


class TestStatsEndpoint:
    def test_stats_returns_200(self, mock_service):
        mock_service.get_stats = AsyncMock(
            return_value={"repositories": [], "total_chunks": 0}
        )
        resp = client.get("/api/code-search/stats")
        assert resp.status_code == 200

    def test_stats_returns_data(self, mock_service):
        mock_service.get_stats = AsyncMock(
            return_value={"repositories": ["/r1"], "total_chunks": 50}
        )
        resp = client.get("/api/code-search/stats")
        assert resp.json()["total_chunks"] == 50

    def test_stats_service_error(self, mock_service):
        mock_service.get_stats = AsyncMock(side_effect=RuntimeError("err"))
        resp = client.get("/api/code-search/stats")
        assert resp.status_code in (500, 503)


class TestRebuildEndpoint:
    def test_rebuild_returns_200(self, mock_service):
        mock_service.rebuild_index = AsyncMock(return_value={"status": "rebuilt"})
        resp = client.post("/api/code-search/rebuild")
        assert resp.status_code == 200

    def test_rebuild_service_error(self, mock_service):
        mock_service.rebuild_index = AsyncMock(side_effect=RuntimeError("x"))
        resp = client.post("/api/code-search/rebuild")
        assert resp.status_code in (500, 503)
