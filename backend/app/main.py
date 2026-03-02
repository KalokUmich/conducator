"""Conducator FastAPI application entry point.

Lifespan initializes:
  * Database connection pool
  * Git Workspace Service (replaces Live Share)
  * CocoIndex Code Search Service (replaces home-built RAG)

Removed in this version:
  * FAISS index loading
  * Bedrock Embeddings initialisation
  * Old RAG module imports
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import AppSettings, _inject_embedding_env_vars, load_settings
from .git_workspace.service import GitWorkspaceService
from .git_workspace.delegate_broker import DelegateBroker
from .code_search.service import CodeSearchService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup / shutdown lifecycle."""
    settings: AppSettings = load_settings()

    # ---- Git Workspace ----
    git_service    = GitWorkspaceService()
    delegate_broker = DelegateBroker()
    if settings.git_workspace.enabled:
        await git_service.initialize(settings.git_workspace)
        logger.info("Git Workspace module initialized.")
    app.state.git_workspace_service = git_service
    app.state.delegate_broker       = delegate_broker

    # ---- CocoIndex Code Search ----
    code_search_service = CodeSearchService()
    if settings.code_search.enabled:
        _inject_embedding_env_vars(settings)    # inject secrets → env vars
        await code_search_service.initialize(settings.code_search)
        logger.info("CocoIndex Code Search initialized.")
    app.state.code_search_service = code_search_service

    logger.info("Conducator startup complete.")
    yield
    # ---- Shutdown ----
    await git_service.shutdown()
    await code_search_service.shutdown()
    logger.info("Conducator shutdown complete.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title       = "Conducator",
        description = "Real-time collaborative coding backend",
        version     = "2.0.0",
        lifespan    = lifespan,
    )

    # --- CORS ---
    _s = settings or load_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins     = _s.server.allowed_origins,
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # --- Routers ---
    from .git_workspace.router import router as git_workspace_router
    from .code_search.router   import router as code_search_router
    from .context.router       import router as context_router
    # (Other existing routers — rooms, auth, users — unchanged)

    app.include_router(git_workspace_router)
    app.include_router(code_search_router)
    app.include_router(context_router)

    return app


# Module-level app instance (used by uvicorn and test fixtures)
app = create_app()
