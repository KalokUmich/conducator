"""Reranking provider abstraction for code search.

Supports four backends:

1. **none**       – No reranking (passthrough, default)
2. **cohere**     – Cohere Rerank 3.5 via API (also available on Bedrock)
3. **bedrock**    – AWS Bedrock Rerank (Cohere Rerank 3.5 model)
4. **cross_encoder** – Local cross-encoder model (ms-marco-MiniLM-L-6-v2)

Reranking is a **post-retrieval** step: the vector search returns top-K
candidates, and the reranker re-scores them against the original query
to produce a more precise final ordering.

Usage::

    reranker = create_rerank_provider(settings)
    reranked = await reranker.rerank(
        query="how does authentication work",
        documents=["chunk1...", "chunk2...", ...],
        top_n=5,
    )
"""
from __future__ import annotations

import abc
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RerankResult:
    """A single reranked document with its relevance score."""
    index: int          # Original position in the input list
    score: float        # Reranker relevance score (higher = more relevant)
    text: str           # The document text (for convenience)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class RerankProvider(abc.ABC):
    """Base class for all reranking backends."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable backend name (e.g. ``"cohere/rerank-v3.5"``)."""

    @abc.abstractmethod
    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: Optional[int] = None,
    ) -> List[RerankResult]:
        """Rerank documents by relevance to *query*.

        Parameters
        ----------
        query:
            The search query string.
        documents:
            Candidate documents to rerank.
        top_n:
            Maximum number of results to return. Defaults to len(documents).

        Returns
        -------
        List[RerankResult]
            Results sorted by relevance score (descending).
        """

    def health_check(self) -> Dict[str, Any]:
        """Return a JSON-friendly dict describing provider status."""
        return {
            "provider": self.name,
            "status": "ok",
        }


# ---------------------------------------------------------------------------
# 1. No-op Passthrough
# ---------------------------------------------------------------------------


class NoopRerankProvider(RerankProvider):
    """Passthrough — returns documents in their original order.

    Used when reranking is disabled.
    """

    @property
    def name(self) -> str:
        return "none"

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: Optional[int] = None,
    ) -> List[RerankResult]:
        n = top_n if top_n is not None else len(documents)
        return [
            RerankResult(index=i, score=1.0 - (i * 0.001), text=doc)
            for i, doc in enumerate(documents[:n])
        ]


# ---------------------------------------------------------------------------
# 2. Cohere Rerank (Direct API)
# ---------------------------------------------------------------------------


class CohereRerankProvider(RerankProvider):
    """Cohere Rerank API (rerank-v3.5).

    Requires ``COHERE_API_KEY`` in env or passed explicitly.
    Pricing: ~$2.00 per 1000 queries (each query up to 100 docs).
    """

    def __init__(
        self,
        model: str = "rerank-v3.5",
        api_key: Optional[str] = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        import cohere  # type: ignore

        kwargs: Dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        self._client = cohere.Client(**kwargs)
        logger.info("CohereRerankProvider initialised model=%s", self._model)

    @property
    def name(self) -> str:
        return f"cohere/{self._model}"

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: Optional[int] = None,
    ) -> List[RerankResult]:
        if not documents:
            return []

        self._ensure_client()
        n = top_n if top_n is not None else len(documents)
        doc_list = list(documents)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.rerank(
                model=self._model,
                query=query,
                documents=doc_list,
                top_n=n,
            ),
        )

        results = []
        for r in response.results:
            results.append(
                RerankResult(
                    index=r.index,
                    score=r.relevance_score,
                    text=doc_list[r.index],
                )
            )
        return results


# ---------------------------------------------------------------------------
# 3. AWS Bedrock Rerank (Cohere Rerank via Bedrock)
# ---------------------------------------------------------------------------


class BedrockRerankProvider(RerankProvider):
    """AWS Bedrock Rerank using Cohere Rerank 3.5.

    Uses the Bedrock ``invoke_model`` API with the Cohere rerank model.
    Pricing: $2.00 per 1000 queries on Bedrock.
    Reuses existing AWS credentials from conductor.secrets.yaml.
    """

    def __init__(
        self,
        model_id: str = "cohere.rerank-v3-5:0",
        region: str = "us-east-1",
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
    ) -> None:
        self._model_id = model_id
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        import boto3  # type: ignore

        kwargs: Dict[str, Any] = {
            "service_name": "bedrock-runtime",
            "region_name": self._region,
        }
        if self._access_key_id and self._secret_access_key:
            kwargs["aws_access_key_id"] = self._access_key_id
            kwargs["aws_secret_access_key"] = self._secret_access_key
        self._client = boto3.client(**kwargs)
        logger.info(
            "BedrockRerankProvider initialised model=%s region=%s",
            self._model_id,
            self._region,
        )

    @property
    def name(self) -> str:
        return f"bedrock/{self._model_id}"

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: Optional[int] = None,
    ) -> List[RerankResult]:
        if not documents:
            return []

        self._ensure_client()
        n = top_n if top_n is not None else len(documents)
        doc_list = list(documents)

        body = json.dumps({
            "query": query,
            "documents": doc_list,
            "top_n": n,
        })

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
        result = json.loads(response["body"].read())

        results = []
        for r in result.get("results", []):
            idx = r["index"]
            results.append(
                RerankResult(
                    index=idx,
                    score=r["relevance_score"],
                    text=doc_list[idx],
                )
            )
        # Sort by score descending (Bedrock may already sort, but be safe)
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:n]


# ---------------------------------------------------------------------------
# 4. Local Cross-Encoder
# ---------------------------------------------------------------------------


class CrossEncoderRerankProvider(RerankProvider):
    """Local cross-encoder reranker using sentence-transformers.

    Default model: ``cross-encoder/ms-marco-MiniLM-L-6-v2``
    (~80 MB, runs on CPU).

    Produces a relevance score for each (query, document) pair.
    """

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    ) -> None:
        self._model_name = model_name
        self._model: Any = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import CrossEncoder  # type: ignore

        self._model = CrossEncoder(self._model_name)
        logger.info(
            "CrossEncoderRerankProvider loaded model=%s", self._model_name
        )

    @property
    def name(self) -> str:
        return f"cross_encoder/{self._model_name}"

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: Optional[int] = None,
    ) -> List[RerankResult]:
        if not documents:
            return []

        self._ensure_loaded()
        n = top_n if top_n is not None else len(documents)
        doc_list = list(documents)

        # Cross-encoder takes (query, doc) pairs
        pairs = [(query, doc) for doc in doc_list]

        loop = asyncio.get_event_loop()
        scores = await loop.run_in_executor(
            None,
            lambda: self._model.predict(pairs),
        )

        # Build results and sort by score
        results = [
            RerankResult(index=i, score=float(scores[i]), text=doc_list[i])
            for i in range(len(doc_list))
        ]
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:n]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_rerank_provider(settings) -> RerankProvider:
    """Instantiate the configured rerank provider.

    Parameters
    ----------
    settings:
        An object with at least ``rerank_backend`` plus the relevant
        model / credential fields (typically ``CodeSearchSettings``).

    Returns
    -------
    RerankProvider
    """
    backend = getattr(settings, "rerank_backend", "none")

    if backend == "none":
        logger.info("Reranking disabled (noop provider).")
        return NoopRerankProvider()

    if backend == "cohere":
        model = getattr(settings, "cohere_rerank_model", "rerank-v3.5")
        api_key = getattr(settings, "cohere_rerank_api_key", None)
        logger.info("Creating CohereRerankProvider (model=%s)", model)
        return CohereRerankProvider(model=model, api_key=api_key)

    if backend == "bedrock":
        model_id = getattr(settings, "bedrock_rerank_model_id", "cohere.rerank-v3-5:0")
        region = getattr(settings, "bedrock_region", "us-east-1")
        ak = getattr(settings, "bedrock_access_key_id", None)
        sk = getattr(settings, "bedrock_secret_access_key", None)
        logger.info("Creating BedrockRerankProvider (model=%s, region=%s)", model_id, region)
        return BedrockRerankProvider(
            model_id=model_id,
            region=region,
            access_key_id=ak,
            secret_access_key=sk,
        )

    if backend == "cross_encoder":
        model = getattr(
            settings, "cross_encoder_model_name",
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
        )
        logger.info("Creating CrossEncoderRerankProvider (model=%s)", model)
        return CrossEncoderRerankProvider(model_name=model)

    raise ValueError(
        f"Unknown rerank backend: {backend!r}. "
        f"Must be one of: none, cohere, bedrock, cross_encoder"
    )
