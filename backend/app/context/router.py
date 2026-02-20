"""Context enrichment router — POST /context/explain."""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.ai_provider.resolver import get_resolver

from .enricher import ContextEnricher
from .schemas import ExplainRequest, ExplainResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/context", tags=["context"])


@router.post("/explain", response_model=ExplainResponse)
async def explain_code(request: ExplainRequest) -> ExplainResponse:
    """Explain a code snippet using AI with workspace context enrichment.

    The extension sends the selected code plus optional context gathered by
    ``ContextGatherer`` (file content, surrounding lines, imports, LSP data).
    The backend fills in any missing fields and calls the active AI provider.

    Args:
        request: ExplainRequest with snippet, file path, and optional context.

    Returns:
        ExplainResponse with the AI explanation.

    Example::

        POST /context/explain
        {
            "room_id": "abc-123",
            "snippet": "def process(data):\\n    return data.split(',')",
            "file_path": "app/processor.py",
            "line_start": 10,
            "line_end": 11,
            "language": "python",
            "surrounding_code": "9: # Process raw input\\n10: def process...",
            "imports": ["import re", "from typing import List"],
            "containing_function": "def process(data):"
        }
    """
    resolver = get_resolver()
    if resolver is None:
        return JSONResponse(
            {"error": "AI provider not available"},
            status_code=503,
        )

    # resolve() returns the active provider (or None if unhealthy)
    provider = resolver.resolve()
    if provider is None:
        return JSONResponse(
            {"error": "No healthy AI provider found"},
            status_code=503,
        )

    try:
        enricher = ContextEnricher(provider=provider)
        response = enricher.explain(request)
        logger.info(
            "[context/explain] Explained %s lines %d-%d via %s",
            request.file_path, request.line_start, request.line_end,
            getattr(provider, "model_id", "unknown"),
        )
        return response
    except Exception as exc:
        logger.exception("[context/explain] Explanation failed: %s", exc)
        return JSONResponse(
            {"error": f"Explanation failed: {exc}"},
            status_code=500,
        )
