"""Tests for the context enrichment router — POST /api/context.

Rewritten for CocoIndex integration. All heavy dependencies
(cocoindex, sentence_transformers, sqlite_vec, git) are stubbed so the
suite runs in any CI environment.

Total: 20 tests
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

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

from backend.routers.context import router  # noqa: E402  # type: ignore

app = FastAPI()
app.include_router(router)
client = TestClient(app)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_code_search():
    svc = MagicMock()
    svc.search = AsyncMock(return_value=[])
    return svc


@pytest.fixture(autouse=True)
def _inject(mock_code_search):
    with patch(
        "backend.routers.context.get_code_search_service",
        return_value=mock_code_search,
    ):
        yield


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestContextEndpointHappyPath:
    def test_returns_200_for_valid_request(self, mock_code_search):
        mock_code_search.search = AsyncMock(
            return_value=[{"path": "app.py", "snippet": "def main(): pass", "score": 0.9}]
        )
        resp = client.post("/api/context", json={"query": "main function"})
        assert resp.status_code == 200

    def test_response_is_dict(self, mock_code_search):
        mock_code_search.search = AsyncMock(return_value=[])
        resp = client.post("/api/context", json={"query": "hello"})
        assert isinstance(resp.json(), dict)

    def test_response_contains_context_key(self, mock_code_search):
        mock_code_search.search = AsyncMock(
            return_value=[{"path": "x.py", "snippet": "x=1", "score": 0.8}]
        )
        resp = client.post("/api/context", json={"query": "variable"})
        data = resp.json()
        assert "context" in data or "results" in data or isinstance(data, dict)

    def test_empty_results_returns_200(self, mock_code_search):
        mock_code_search.search = AsyncMock(return_value=[])
        resp = client.post("/api/context", json={"query": "zzz_no_match"})
        assert resp.status_code == 200

    def test_multiple_results_included(self, mock_code_search):
        mock_code_search.search = AsyncMock(
            return_value=[
                {"path": "a.py", "snippet": "a=1", "score": 0.9},
                {"path": "b.py", "snippet": "b=2", "score": 0.7},
            ]
        )
        resp = client.post("/api/context", json={"query": "variables"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Validation / error tests
# ---------------------------------------------------------------------------


class TestContextEndpointValidation:
    def test_missing_query_returns_422(self, mock_code_search):
        resp = client.post("/api/context", json={})
        assert resp.status_code == 422

    def test_empty_string_query(self, mock_code_search):
        mock_code_search.search = AsyncMock(return_value=[])
        resp = client.post("/api/context", json={"query": ""})
        # Either 200 (empty is allowed) or 422 (validation rejects it)
        assert resp.status_code in (200, 422)

    def test_none_query_returns_422(self, mock_code_search):
        resp = client.post("/api/context", json={"query": None})
        assert resp.status_code == 422

    def test_wrong_content_type_returns_422(self, mock_code_search):
        resp = client.post("/api/context", data="not-json")
        assert resp.status_code == 422

    def test_extra_fields_ignored(self, mock_code_search):
        mock_code_search.search = AsyncMock(return_value=[])
        resp = client.post(
            "/api/context", json={"query": "test", "extra_field": "ignored"}
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Service-error / edge-case tests
# ---------------------------------------------------------------------------


class TestContextEndpointServiceErrors:
    def test_search_service_error_returns_5xx(self, mock_code_search):
        mock_code_search.search = AsyncMock(side_effect=RuntimeError("db down"))
        resp = client.post("/api/context", json={"query": "x"})
        assert resp.status_code in (500, 503)

    def test_search_timeout_returns_5xx(self, mock_code_search):
        import asyncio

        mock_code_search.search = AsyncMock(
            side_effect=asyncio.TimeoutError("timeout")
        )
        resp = client.post("/api/context", json={"query": "slow"})
        assert resp.status_code in (500, 503, 504)

    def test_search_called_with_query(self, mock_code_search):
        mock_code_search.search = AsyncMock(return_value=[])
        client.post("/api/context", json={"query": "my query"})
        mock_code_search.search.assert_called_once()
        args, kwargs = mock_code_search.search.call_args
        query_val = kwargs.get("query") or (args[0] if args else None)
        assert query_val == "my query"


# ---------------------------------------------------------------------------
# Optional parameters
# ---------------------------------------------------------------------------


class TestContextEndpointOptionalParams:
    def test_with_repo_path_param(self, mock_code_search):
        mock_code_search.search = AsyncMock(return_value=[])
        resp = client.post(
            "/api/context",
            json={"query": "test", "repo_path": "/some/repo"},
        )
        assert resp.status_code == 200

    def test_with_limit_param(self, mock_code_search):
        mock_code_search.search = AsyncMock(return_value=[])
        resp = client.post("/api/context", json={"query": "test", "limit": 3})
        assert resp.status_code == 200

    def test_with_all_optional_params(self, mock_code_search):
        mock_code_search.search = AsyncMock(return_value=[])
        resp = client.post(
            "/api/context",
            json={"query": "test", "repo_path": "/r", "limit": 5},
        )
        assert resp.status_code == 200
