"""Embeddings configuration endpoint.

Exposes the active embedding model configuration so the VS Code extension
can stay in sync with the backend's conductor.settings.yaml.

Endpoint:
    GET /embeddings/config  — returns {model, dim, provider}
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/embeddings", tags=["embeddings"])

# Map full LiteLLM model strings → embedding dimensions.
# Mirrors the _KNOWN_DIMS table in code_search/embedding_provider.py.
_KNOWN_DIMS: dict[str, int] = {
    # Local (SentenceTransformers via sbert/ prefix)
    "sbert/sentence-transformers/all-MiniLM-L6-v2": 384,
    "sbert/nomic-ai/CodeRankEmbed":                 768,
    # AWS Bedrock
    "bedrock/cohere.embed-english-v3":              1024,
    "bedrock/cohere.embed-multilingual-v3":         1024,
    "bedrock/cohere.embed-v4:0":                    1024,
    "bedrock/amazon.titan-embed-text-v1":           1536,
    "bedrock/amazon.titan-embed-text-v2:0":         1024,
    # OpenAI
    "text-embedding-3-small":                       1536,
    "text-embedding-3-large":                       3072,
    "text-embedding-ada-002":                       1536,
    # Voyage AI
    "voyage/voyage-code-3":                         1024,
    "voyage/voyage-3":                              1024,
    "voyage/voyage-3-lite":                         512,
    # Mistral
    "mistral/codestral-embed-2505":                 1024,
    "mistral/mistral-embed":                        1024,
    # Cohere (direct)
    "cohere/embed-english-v3.0":                    1024,
    "cohere/embed-english-v4.0":                    1024,
    # Google Gemini
    "gemini/text-embedding-004":                    768,
    # Ollama
    "ollama/nomic-embed-text":                      768,
}

_DEFAULT_DIMS = 1024


def _parse_model_string(model_str: str) -> tuple[str, str]:
    """Split a LiteLLM model string into (provider, model_name).

    Examples::

        "bedrock/cohere.embed-v4:0"                     → ("bedrock", "cohere.embed-v4:0")
        "sbert/sentence-transformers/all-MiniLM-L6-v2"  → ("local",   "sentence-transformers/all-MiniLM-L6-v2")
        "text-embedding-3-small"                         → ("openai",  "text-embedding-3-small")
    """
    if model_str.startswith("sbert/"):
        return "local", model_str[len("sbert/"):]

    if "/" in model_str:
        provider, _, rest = model_str.partition("/")
        return provider, rest

    # No prefix → OpenAI convention
    return "openai", model_str


class EmbeddingConfigResponse(BaseModel):
    """Embedding configuration returned by GET /embeddings/config."""
    model:    str
    dim:      int
    provider: str


@router.get("/config", response_model=EmbeddingConfigResponse)
async def get_embedding_config() -> EmbeddingConfigResponse:
    """Return the active embedding model configuration.

    Reads ``code_search.embedding_model`` from ``conductor.settings.yaml``
    so the VS Code extension can stay in sync without hard-coding defaults.
    """
    from app.config import load_settings

    settings = load_settings()
    model_str = settings.code_search.embedding_model

    provider, model_name = _parse_model_string(model_str)
    dim = (
        settings.code_search.embedding_dimensions
        or _KNOWN_DIMS.get(model_str, _DEFAULT_DIMS)
    )

    return EmbeddingConfigResponse(model=model_name, dim=dim, provider=provider)

