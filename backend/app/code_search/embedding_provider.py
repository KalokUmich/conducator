"""Embedding provider abstraction for code search.

Supports five backends:

1. **local**    – SentenceTransformers (free, runs on CPU/GPU)
2. **bedrock**  – AWS Bedrock (Cohere Embed v4 default, Titan optional)
3. **openai**   – OpenAI Embeddings API
4. **voyage**   – Voyage AI (code-specialised models)
5. **mistral**  – Mistral Embeddings (Codestral Embed)

The default backend is ``bedrock`` with Cohere Embed v4
(``cohere.embed-v4:0``).

Usage::

    provider = create_embedding_provider(settings)
    vectors  = await provider.embed_texts(["def main(): pass"])
    dims     = provider.dimensions
"""
from __future__ import annotations

import abc
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class EmbeddingProvider(abc.ABC):
    """Base class for all embedding backends."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable backend name (e.g. ``"bedrock/cohere"``).."""

    @property
    @abc.abstractmethod
    def dimensions(self) -> int:
        """Dimensionality of the embedding vectors produced by this model."""

    @abc.abstractmethod
    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Embed a batch of texts.

        Parameters
        ----------
        texts:
            One or more text strings to embed.

        Returns
        -------
        np.ndarray
            Shape ``(len(texts), self.dimensions)`` float32 array.
        """

    async def embed_query(self, query: str) -> np.ndarray:
        """Embed a single search query.

        Some models use a different prefix / input type for queries.
        Override this if needed; default delegates to *embed_texts*.
        """
        result = await self.embed_texts([query])
        return result[0]

    def health_check(self) -> Dict[str, Any]:
        """Return a JSON-friendly dict describing provider status."""
        return {
            "provider": self.name,
            "dimensions": self.dimensions,
            "status": "ok",
        }


# ---------------------------------------------------------------------------
# 1. Local (SentenceTransformers)
# ---------------------------------------------------------------------------


class LocalEmbeddingProvider(EmbeddingProvider):
    """SentenceTransformers model running locally.

    Default model: ``all-MiniLM-L6-v2`` (384-d, ~80 MB).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model: Any = None  # lazy-loaded
        self._dims: Optional[int] = None

    # -- lazy load ----------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer  # type: ignore

        self._model = SentenceTransformer(self._model_name)
        # Probe dimensions with a dummy encode
        dummy = self._model.encode(["test"], convert_to_numpy=True)
        self._dims = dummy.shape[1]
        logger.info(
            "LocalEmbeddingProvider loaded model=%s dims=%d",
            self._model_name,
            self._dims,
        )

    # -- interface ----------------------------------------------------------

    @property
    def name(self) -> str:
        return f"local/{self._model_name}"

    @property
    def dimensions(self) -> int:
        self._ensure_loaded()
        assert self._dims is not None
        return self._dims

    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        self._ensure_loaded()
        loop = asyncio.get_event_loop()
        vectors = await loop.run_in_executor(
            None,
            lambda: self._model.encode(list(texts), convert_to_numpy=True),
        )
        return vectors.astype(np.float32)


# ---------------------------------------------------------------------------
# 2. Bedrock (Cohere Embed v4 / Titan)
# ---------------------------------------------------------------------------


class BedrockEmbeddingProvider(EmbeddingProvider):
    """AWS Bedrock embedding models.

    Default: ``cohere.embed-v4:0`` (1024-d, 128K context, $0.12 / 1M tokens).
    Also supports Titan Embed V2 (``amazon.titan-embed-text-v2:0``, 1024-d).
    """

    # Known dimension map (avoids an API call at init time)
    _DIM_MAP: Dict[str, int] = {
        "cohere.embed-english-v3":     1024,
        "cohere.embed-multilingual-v3": 1024,
        "cohere.embed-v4:0":          1024,
        "amazon.titan-embed-text-v1":  1536,
        "amazon.titan-embed-text-v2:0": 1024,
    }

    def __init__(
        self,
        model_id: str = "cohere.embed-v4:0",
        region: str = "us-east-1",
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
    ) -> None:
        self._model_id = model_id
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._client: Any = None  # lazy boto3 client

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        import boto3  # type: ignore

        kwargs: Dict[str, Any] = {"service_name": "bedrock-runtime", "region_name": self._region}
        if self._access_key_id and self._secret_access_key:
            kwargs["aws_access_key_id"] = self._access_key_id
            kwargs["aws_secret_access_key"] = self._secret_access_key
        self._client = boto3.client(**kwargs)
        logger.info(
            "BedrockEmbeddingProvider initialised model=%s region=%s",
            self._model_id,
            self._region,
        )

    @property
    def name(self) -> str:
        return f"bedrock/{self._model_id}"

    @property
    def dimensions(self) -> int:
        return self._DIM_MAP.get(self._model_id, 1024)

    def _build_body(self, texts: List[str], input_type: str = "search_document") -> str:
        """Build model-specific request body."""
        if self._model_id.startswith("cohere."):
            return json.dumps({
                "texts": texts,
                "input_type": input_type,
                "truncate": "END",
            })
        # Titan — single-text API, caller must batch manually
        return json.dumps({"inputText": texts[0]})

    async def _invoke(self, body: str) -> Dict[str, Any]:
        self._ensure_client()
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.invoke_model(
                modelId=self._model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            ),
        )
        return json.loads(response["body"].read())

    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        text_list = list(texts)
        if self._model_id.startswith("cohere."):
            # Cohere supports batch natively
            body = self._build_body(text_list, input_type="search_document")
            result = await self._invoke(body)
            return np.array(result["embeddings"], dtype=np.float32)
        else:
            # Titan: one call per text
            vectors = []
            for t in text_list:
                body = self._build_body([t])
                result = await self._invoke(body)
                vectors.append(result["embedding"])
            return np.array(vectors, dtype=np.float32)

    async def embed_query(self, query: str) -> np.ndarray:
        if self._model_id.startswith("cohere."):
            body = self._build_body([query], input_type="search_query")
            result = await self._invoke(body)
            return np.array(result["embeddings"][0], dtype=np.float32)
        # Titan
        body = self._build_body([query])
        result = await self._invoke(body)
        return np.array(result["embedding"], dtype=np.float32)


# ---------------------------------------------------------------------------
# 3. OpenAI
# ---------------------------------------------------------------------------


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI Embeddings API (e.g. ``text-embedding-3-small``).

    Requires ``OPENAI_API_KEY`` in env or passed explicitly.
    """

    _DIM_MAP: Dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        import openai  # type: ignore

        kwargs: Dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        self._client = openai.OpenAI(**kwargs)
        logger.info("OpenAIEmbeddingProvider initialised model=%s", self._model_name)

    @property
    def name(self) -> str:
        return f"openai/{self._model_name}"

    @property
    def dimensions(self) -> int:
        return self._DIM_MAP.get(self._model_name, 1536)

    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        self._ensure_client()
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.embeddings.create(
                model=self._model_name,
                input=list(texts),
            ),
        )
        vectors = [d.embedding for d in response.data]
        return np.array(vectors, dtype=np.float32)


# ---------------------------------------------------------------------------
# 4. Voyage AI
# ---------------------------------------------------------------------------


class VoyageEmbeddingProvider(EmbeddingProvider):
    """Voyage AI embedding models, specialised for code.

    Default model: ``voyage-code-3`` (1024-d).
    Requires ``VOYAGE_API_KEY`` in env or passed explicitly.
    """

    _DIM_MAP: Dict[str, int] = {
        "voyage-code-3":  1024,
        "voyage-3":       1024,
        "voyage-3-lite":  512,
    }

    def __init__(
        self,
        model_name: str = "voyage-code-3",
        api_key: Optional[str] = None,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        import voyageai  # type: ignore

        kwargs: Dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        self._client = voyageai.Client(**kwargs)
        logger.info("VoyageEmbeddingProvider initialised model=%s", self._model_name)

    @property
    def name(self) -> str:
        return f"voyage/{self._model_name}"

    @property
    def dimensions(self) -> int:
        return self._DIM_MAP.get(self._model_name, 1024)

    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        self._ensure_client()
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.embed(
                list(texts),
                model=self._model_name,
                input_type="document",
            ),
        )
        return np.array(response.embeddings, dtype=np.float32)

    async def embed_query(self, query: str) -> np.ndarray:
        self._ensure_client()
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.embed(
                [query],
                model=self._model_name,
                input_type="query",
            ),
        )
        return np.array(response.embeddings[0], dtype=np.float32)


# ---------------------------------------------------------------------------
# 5. Mistral
# ---------------------------------------------------------------------------


class MistralEmbeddingProvider(EmbeddingProvider):
    """Mistral Embeddings API (Codestral Embed).

    Default model: ``codestral-embed-2505`` (1024-d).
    Requires ``MISTRAL_API_KEY`` in env or passed explicitly.
    """

    _DIM_MAP: Dict[str, int] = {
        "codestral-embed-2505": 1024,
        "mistral-embed":        1024,
    }

    def __init__(
        self,
        model_name: str = "codestral-embed-2505",
        api_key: Optional[str] = None,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        from mistralai import Mistral  # type: ignore

        kwargs: Dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        self._client = Mistral(**kwargs)
        logger.info("MistralEmbeddingProvider initialised model=%s", self._model_name)

    @property
    def name(self) -> str:
        return f"mistral/{self._model_name}"

    @property
    def dimensions(self) -> int:
        return self._DIM_MAP.get(self._model_name, 1024)

    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        self._ensure_client()
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.embeddings.create(
                model=self._model_name,
                inputs=list(texts),
            ),
        )
        vectors = [d.embedding for d in response.data]
        return np.array(vectors, dtype=np.float32)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_embedding_provider(settings) -> EmbeddingProvider:
    """Instantiate the configured embedding provider.

    Parameters
    ----------
    settings:
        An object with at least ``embedding_backend`` plus the relevant
        model / credential fields (typically ``CodeSearchSettings``).

    Returns
    -------
    EmbeddingProvider
    """
    backend = getattr(settings, "embedding_backend", "bedrock")

    if backend == "local":
        model = getattr(settings, "local_model_name", "all-MiniLM-L6-v2")
        logger.info("Creating LocalEmbeddingProvider (model=%s)", model)
        return LocalEmbeddingProvider(model_name=model)

    if backend == "bedrock":
        model_id = getattr(settings, "bedrock_model_id", "cohere.embed-v4:0")
        region = getattr(settings, "bedrock_region", "us-east-1")
        ak = getattr(settings, "bedrock_access_key_id", None)
        sk = getattr(settings, "bedrock_secret_access_key", None)
        logger.info("Creating BedrockEmbeddingProvider (model=%s, region=%s)", model_id, region)
        return BedrockEmbeddingProvider(
            model_id=model_id,
            region=region,
            access_key_id=ak,
            secret_access_key=sk,
        )

    if backend == "openai":
        model = getattr(settings, "openai_model_name", "text-embedding-3-small")
        api_key = getattr(settings, "openai_api_key", None)
        logger.info("Creating OpenAIEmbeddingProvider (model=%s)", model)
        return OpenAIEmbeddingProvider(model_name=model, api_key=api_key)

    if backend == "voyage":
        model = getattr(settings, "voyage_model_name", "voyage-code-3")
        api_key = getattr(settings, "voyage_api_key", None)
        logger.info("Creating VoyageEmbeddingProvider (model=%s)", model)
        return VoyageEmbeddingProvider(model_name=model, api_key=api_key)

    if backend == "mistral":
        model = getattr(settings, "mistral_model_name", "codestral-embed-2505")
        api_key = getattr(settings, "mistral_api_key", None)
        logger.info("Creating MistralEmbeddingProvider (model=%s)", model)
        return MistralEmbeddingProvider(model_name=model, api_key=api_key)

    raise ValueError(
        f"Unknown embedding backend: {backend!r}. "
        f"Must be one of: local, bedrock, openai, voyage, mistral"
    )
