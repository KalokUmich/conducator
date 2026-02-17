"""Shared test fixtures and configuration for backend tests."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def api_client():
    """Provide a TestClient for the main FastAPI app.

    Named api_client (not client) to avoid shadowing the module-level
    `client = TestClient(app)` pattern used in existing test files.
    """
    return TestClient(app)
