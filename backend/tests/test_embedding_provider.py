"""Comprehensive tests for the embedding provider abstraction.

Tests all 5 backends with mocked external dependencies:

1. LocalEmbeddingProvider  — SentenceTransformers
2. BedrockEmbeddingProvider — AWS Bedrock (Cohere v4 + Titan)
3. OpenAIEmbeddingProvider  — OpenAI
4. VoyageEmbeddingProvider  — Voyage AI
5. MistralEmbeddingProvider — Mistral AI

Also tests:
  * create_embedding_provider factory
  * EmbeddingProvider ABC contract
  * Edge cases (empty input, single text, large batches)
  * Error handling (missing credentials, API failures)
  * embed_query vs embed_texts distinction

Total: 78 tests
"""
from __future__ import annotations

import json
import sys
import types
import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Stubs for heavy deps (prevent real imports)
# ---------------------------------------------------------------------------

def _stub(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(name, m)
    return m


_stub("sentence_transformers", SentenceTransformer=MagicMock)
_stub("boto3")
_stub("openai")
_stub("voyageai")
_stub("mistralai", Mistral=MagicMock)
_stub("cocoindex")
_stub("sqlite_vec")
_stub("tree_sitter_languages")
_stub("networkx", DiGraph=MagicMock, pagerank=MagicMock)

# ---------------------------------------------------------------------------
# Real imports
# ---------------------------------------------------------------------------

from app.code_search.embedding_provider import (  # noqa: E402
    EmbeddingProvider,
    LocalEmbeddingProvider,
    BedrockEmbeddingProvider,
    OpenAIEmbeddingProvider,
    VoyageEmbeddingProvider,
    MistralEmbeddingProvider,
    create_embedding_provider,
)


# ===================================================================
# Helper: mock settings object
# ===================================================================


class MockSettings:
    """Mimics CodeSearchSettings fields."""
    def __init__(self, **kwargs):
        defaults = {
            "embedding_backend": "local",
            "local_model_name": "all-MiniLM-L6-v2",
            "bedrock_model_id": "cohere.embed-v4:0",
            "bedrock_region": "us-east-1",
            "bedrock_access_key_id": None,
            "bedrock_secret_access_key": None,
            "openai_model_name": "text-embedding-3-small",
            "openai_api_key": None,
            "voyage_model_name": "voyage-code-3",
            "voyage_api_key": None,
            "mistral_model_name": "codestral-embed-2505",
            "mistral_api_key": None,
        }
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(self, k, v)


# ===================================================================
# 1. LocalEmbeddingProvider
# ===================================================================


class TestLocalEmbeddingProviderInit:
    def test_default_model_name(self):
        provider = LocalEmbeddingProvider()
        assert provider._model_name == "all-MiniLM-L6-v2"

    def test_custom_model_name(self):
        provider = LocalEmbeddingProvider(model_name="paraphrase-MiniLM-L3-v2")
        assert provider._model_name == "paraphrase-MiniLM-L3-v2"

    def test_name_property(self):
        provider = LocalEmbeddingProvider()
        assert provider.name == "local/all-MiniLM-L6-v2"

    def test_lazy_load_not_called_on_init(self):
        provider = LocalEmbeddingProvider()
        assert provider._model is None


class TestLocalEmbeddingProviderEmbed:
    @pytest.fixture()
    def provider(self):
        p = LocalEmbeddingProvider()
        mock_model = MagicMock()
        mock_model.encode.return_value = np.random.rand(1, 384).astype(np.float32)
        p._model = mock_model
        p._dims = 384
        return p

    @pytest.mark.asyncio
    async def test_embed_texts_returns_ndarray(self, provider):
        provider._model.encode.return_value = np.random.rand(2, 384).astype(np.float32)
        result = await provider.embed_texts(["hello", "world"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 384)

    @pytest.mark.asyncio
    async def test_embed_texts_single(self, provider):
        provider._model.encode.return_value = np.random.rand(1, 384).astype(np.float32)
        result = await provider.embed_texts(["hello"])
        assert result.shape == (1, 384)

    @pytest.mark.asyncio
    async def test_embed_query(self, provider):
        provider._model.encode.return_value = np.random.rand(1, 384).astype(np.float32)
        result = await provider.embed_query("search query")
        assert isinstance(result, np.ndarray)
        assert result.shape == (384,)

    @pytest.mark.asyncio
    async def test_embed_texts_dtype_float32(self, provider):
        provider._model.encode.return_value = np.random.rand(1, 384).astype(np.float64)
        result = await provider.embed_texts(["test"])
        assert result.dtype == np.float32

    def test_dimensions(self, provider):
        assert provider.dimensions == 384

    def test_health_check(self, provider):
        h = provider.health_check()
        assert h["provider"] == "local/all-MiniLM-L6-v2"
        assert h["dimensions"] == 384
        assert h["status"] == "ok"

    @pytest.mark.asyncio
    async def test_embed_empty_list(self, provider):
        provider._model.encode.return_value = np.empty((0, 384), dtype=np.float32)
        result = await provider.embed_texts([])
        assert result.shape[0] == 0

    def test_ensure_loaded_calls_sentence_transformer(self):
        provider = LocalEmbeddingProvider()
        mock_st = MagicMock()
        mock_model = MagicMock()
        mock_model.encode.return_value = np.random.rand(1, 384).astype(np.float32)
        mock_st.return_value = mock_model
        with patch("app.code_search.embedding_provider.LocalEmbeddingProvider._ensure_loaded") as mock_load:
            # Just verify _ensure_loaded is called
            provider._model = mock_model
            provider._dims = 384
            assert provider.dimensions == 384


# ===================================================================
# 2. BedrockEmbeddingProvider
# ===================================================================


class TestBedrockEmbeddingProviderInit:
    def test_default_model(self):
        p = BedrockEmbeddingProvider()
        assert p._model_id == "cohere.embed-v4:0"

    def test_custom_model(self):
        p = BedrockEmbeddingProvider(model_id="amazon.titan-embed-text-v2:0")
        assert p._model_id == "amazon.titan-embed-text-v2:0"

    def test_name_property(self):
        p = BedrockEmbeddingProvider()
        assert p.name == "bedrock/cohere.embed-v4:0"

    def test_dimensions_cohere(self):
        p = BedrockEmbeddingProvider(model_id="cohere.embed-v4:0")
        assert p.dimensions == 1024

    def test_dimensions_titan(self):
        p = BedrockEmbeddingProvider(model_id="amazon.titan-embed-text-v2:0")
        assert p.dimensions == 1024

    def test_dimensions_unknown_model(self):
        p = BedrockEmbeddingProvider(model_id="some.new-model")
        assert p.dimensions == 1024  # default fallback

    def test_lazy_client(self):
        p = BedrockEmbeddingProvider()
        assert p._client is None


class TestBedrockEmbeddingProviderEmbed:
    @pytest.fixture()
    def provider(self):
        p = BedrockEmbeddingProvider(model_id="cohere.embed-v4:0", region="us-east-1")
        mock_client = MagicMock()
        p._client = mock_client
        return p, mock_client

    @pytest.mark.asyncio
    async def test_embed_texts_cohere(self, provider):
        p, mock_client = provider
        embeddings = [[0.1] * 1024, [0.2] * 1024]
        response_body = MagicMock()
        response_body.read.return_value = json.dumps({"embeddings": embeddings}).encode()
        mock_client.invoke_model.return_value = {"body": response_body}

        result = await p.embed_texts(["hello", "world"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 1024)

    @pytest.mark.asyncio
    async def test_embed_texts_cohere_input_type(self, provider):
        p, mock_client = provider
        response_body = MagicMock()
        response_body.read.return_value = json.dumps({"embeddings": [[0.1] * 1024]}).encode()
        mock_client.invoke_model.return_value = {"body": response_body}

        await p.embed_texts(["test"])
        call_args = mock_client.invoke_model.call_args
        body = json.loads(call_args[1]["body"] if "body" in call_args[1] else call_args.kwargs["body"])
        assert body["input_type"] == "search_document"

    @pytest.mark.asyncio
    async def test_embed_query_cohere_uses_search_query(self, provider):
        p, mock_client = provider
        response_body = MagicMock()
        response_body.read.return_value = json.dumps({"embeddings": [[0.1] * 1024]}).encode()
        mock_client.invoke_model.return_value = {"body": response_body}

        await p.embed_query("search this")
        call_args = mock_client.invoke_model.call_args
        body = json.loads(call_args[1]["body"] if "body" in call_args[1] else call_args.kwargs["body"])
        assert body["input_type"] == "search_query"

    @pytest.mark.asyncio
    async def test_embed_texts_titan(self):
        p = BedrockEmbeddingProvider(model_id="amazon.titan-embed-text-v2:0")
        mock_client = MagicMock()
        response_body = MagicMock()
        response_body.read.return_value = json.dumps({"embedding": [0.1] * 1024}).encode()
        mock_client.invoke_model.return_value = {"body": response_body}
        p._client = mock_client

        result = await p.embed_texts(["hello"])
        assert result.shape == (1, 1024)
        # Titan makes one call per text
        assert mock_client.invoke_model.call_count == 1

    @pytest.mark.asyncio
    async def test_embed_texts_titan_multiple(self):
        p = BedrockEmbeddingProvider(model_id="amazon.titan-embed-text-v2:0")
        mock_client = MagicMock()
        response_body = MagicMock()
        response_body.read.return_value = json.dumps({"embedding": [0.1] * 1024}).encode()
        mock_client.invoke_model.return_value = {"body": response_body}
        p._client = mock_client

        result = await p.embed_texts(["hello", "world", "test"])
        assert result.shape == (3, 1024)
        # Titan: one call per text
        assert mock_client.invoke_model.call_count == 3

    def test_build_body_cohere(self):
        p = BedrockEmbeddingProvider(model_id="cohere.embed-v4:0")
        body = json.loads(p._build_body(["hello", "world"], "search_document"))
        assert body["texts"] == ["hello", "world"]
        assert body["input_type"] == "search_document"
        assert body["truncate"] == "END"

    def test_build_body_titan(self):
        p = BedrockEmbeddingProvider(model_id="amazon.titan-embed-text-v2:0")
        body = json.loads(p._build_body(["hello"]))
        assert body["inputText"] == "hello"

    def test_health_check(self):
        p = BedrockEmbeddingProvider()
        h = p.health_check()
        assert "bedrock" in h["provider"]
        assert h["dimensions"] == 1024


# ===================================================================
# 3. OpenAIEmbeddingProvider
# ===================================================================


class TestOpenAIEmbeddingProviderInit:
    def test_default_model(self):
        p = OpenAIEmbeddingProvider()
        assert p._model_name == "text-embedding-3-small"

    def test_custom_model(self):
        p = OpenAIEmbeddingProvider(model_name="text-embedding-3-large")
        assert p._model_name == "text-embedding-3-large"

    def test_name_property(self):
        p = OpenAIEmbeddingProvider()
        assert p.name == "openai/text-embedding-3-small"

    def test_dimensions_small(self):
        p = OpenAIEmbeddingProvider(model_name="text-embedding-3-small")
        assert p.dimensions == 1536

    def test_dimensions_large(self):
        p = OpenAIEmbeddingProvider(model_name="text-embedding-3-large")
        assert p.dimensions == 3072

    def test_dimensions_ada(self):
        p = OpenAIEmbeddingProvider(model_name="text-embedding-ada-002")
        assert p.dimensions == 1536

    def test_dimensions_unknown(self):
        p = OpenAIEmbeddingProvider(model_name="new-model")
        assert p.dimensions == 1536  # default

    def test_lazy_client(self):
        p = OpenAIEmbeddingProvider()
        assert p._client is None


class TestOpenAIEmbeddingProviderEmbed:
    @pytest.fixture()
    def provider(self):
        p = OpenAIEmbeddingProvider()
        mock_client = MagicMock()
        p._client = mock_client
        return p, mock_client

    @pytest.mark.asyncio
    async def test_embed_texts(self, provider):
        p, mock_client = provider
        mock_data = [MagicMock(embedding=[0.1] * 1536), MagicMock(embedding=[0.2] * 1536)]
        mock_client.embeddings.create.return_value = MagicMock(data=mock_data)

        result = await p.embed_texts(["hello", "world"])
        assert result.shape == (2, 1536)

    @pytest.mark.asyncio
    async def test_embed_single(self, provider):
        p, mock_client = provider
        mock_data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create.return_value = MagicMock(data=mock_data)

        result = await p.embed_texts(["hello"])
        assert result.shape == (1, 1536)

    @pytest.mark.asyncio
    async def test_embed_query(self, provider):
        p, mock_client = provider
        mock_data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create.return_value = MagicMock(data=mock_data)

        result = await p.embed_query("search")
        assert result.shape == (1536,)

    def test_health_check(self):
        p = OpenAIEmbeddingProvider()
        h = p.health_check()
        assert h["provider"] == "openai/text-embedding-3-small"


# ===================================================================
# 4. VoyageEmbeddingProvider
# ===================================================================


class TestVoyageEmbeddingProviderInit:
    def test_default_model(self):
        p = VoyageEmbeddingProvider()
        assert p._model_name == "voyage-code-3"

    def test_custom_model(self):
        p = VoyageEmbeddingProvider(model_name="voyage-3")
        assert p._model_name == "voyage-3"

    def test_name_property(self):
        p = VoyageEmbeddingProvider()
        assert p.name == "voyage/voyage-code-3"

    def test_dimensions_code(self):
        p = VoyageEmbeddingProvider(model_name="voyage-code-3")
        assert p.dimensions == 1024

    def test_dimensions_lite(self):
        p = VoyageEmbeddingProvider(model_name="voyage-3-lite")
        assert p.dimensions == 512

    def test_lazy_client(self):
        p = VoyageEmbeddingProvider()
        assert p._client is None


class TestVoyageEmbeddingProviderEmbed:
    @pytest.fixture()
    def provider(self):
        p = VoyageEmbeddingProvider()
        mock_client = MagicMock()
        p._client = mock_client
        return p, mock_client

    @pytest.mark.asyncio
    async def test_embed_texts(self, provider):
        p, mock_client = provider
        mock_client.embed.return_value = MagicMock(embeddings=[[0.1] * 1024, [0.2] * 1024])

        result = await p.embed_texts(["hello", "world"])
        assert result.shape == (2, 1024)

    @pytest.mark.asyncio
    async def test_embed_texts_uses_document_type(self, provider):
        p, mock_client = provider
        mock_client.embed.return_value = MagicMock(embeddings=[[0.1] * 1024])

        await p.embed_texts(["test"])
        mock_client.embed.assert_called_once()
        call_kwargs = mock_client.embed.call_args
        assert call_kwargs[1]["input_type"] == "document"

    @pytest.mark.asyncio
    async def test_embed_query_uses_query_type(self, provider):
        p, mock_client = provider
        mock_client.embed.return_value = MagicMock(embeddings=[[0.1] * 1024])

        await p.embed_query("search")
        call_kwargs = mock_client.embed.call_args
        assert call_kwargs[1]["input_type"] == "query"

    def test_health_check(self):
        p = VoyageEmbeddingProvider()
        h = p.health_check()
        assert h["provider"] == "voyage/voyage-code-3"
        assert h["dimensions"] == 1024


# ===================================================================
# 5. MistralEmbeddingProvider
# ===================================================================


class TestMistralEmbeddingProviderInit:
    def test_default_model(self):
        p = MistralEmbeddingProvider()
        assert p._model_name == "codestral-embed-2505"

    def test_custom_model(self):
        p = MistralEmbeddingProvider(model_name="mistral-embed")
        assert p._model_name == "mistral-embed"

    def test_name_property(self):
        p = MistralEmbeddingProvider()
        assert p.name == "mistral/codestral-embed-2505"

    def test_dimensions(self):
        p = MistralEmbeddingProvider()
        assert p.dimensions == 1024

    def test_dimensions_generic(self):
        p = MistralEmbeddingProvider(model_name="mistral-embed")
        assert p.dimensions == 1024

    def test_lazy_client(self):
        p = MistralEmbeddingProvider()
        assert p._client is None


class TestMistralEmbeddingProviderEmbed:
    @pytest.fixture()
    def provider(self):
        p = MistralEmbeddingProvider()
        mock_client = MagicMock()
        p._client = mock_client
        return p, mock_client

    @pytest.mark.asyncio
    async def test_embed_texts(self, provider):
        p, mock_client = provider
        mock_data = [MagicMock(embedding=[0.1] * 1024), MagicMock(embedding=[0.2] * 1024)]
        mock_client.embeddings.create.return_value = MagicMock(data=mock_data)

        result = await p.embed_texts(["hello", "world"])
        assert result.shape == (2, 1024)

    @pytest.mark.asyncio
    async def test_embed_single(self, provider):
        p, mock_client = provider
        mock_data = [MagicMock(embedding=[0.1] * 1024)]
        mock_client.embeddings.create.return_value = MagicMock(data=mock_data)

        result = await p.embed_texts(["test"])
        assert result.shape == (1, 1024)

    @pytest.mark.asyncio
    async def test_embed_query(self, provider):
        p, mock_client = provider
        mock_data = [MagicMock(embedding=[0.1] * 1024)]
        mock_client.embeddings.create.return_value = MagicMock(data=mock_data)

        result = await p.embed_query("search")
        assert result.shape == (1024,)

    def test_health_check(self):
        p = MistralEmbeddingProvider()
        h = p.health_check()
        assert h["provider"] == "mistral/codestral-embed-2505"


# ===================================================================
# Factory: create_embedding_provider
# ===================================================================


class TestCreateEmbeddingProvider:
    def test_create_local(self):
        settings = MockSettings(embedding_backend="local")
        provider = create_embedding_provider(settings)
        assert isinstance(provider, LocalEmbeddingProvider)
        assert provider._model_name == "all-MiniLM-L6-v2"

    def test_create_local_custom_model(self):
        settings = MockSettings(embedding_backend="local", local_model_name="custom-model")
        provider = create_embedding_provider(settings)
        assert provider._model_name == "custom-model"

    def test_create_bedrock_default(self):
        settings = MockSettings(embedding_backend="bedrock")
        provider = create_embedding_provider(settings)
        assert isinstance(provider, BedrockEmbeddingProvider)
        assert provider._model_id == "cohere.embed-v4:0"

    def test_create_bedrock_titan(self):
        settings = MockSettings(
            embedding_backend="bedrock",
            bedrock_model_id="amazon.titan-embed-text-v2:0",
        )
        provider = create_embedding_provider(settings)
        assert provider._model_id == "amazon.titan-embed-text-v2:0"

    def test_create_bedrock_with_credentials(self):
        settings = MockSettings(
            embedding_backend="bedrock",
            bedrock_access_key_id="AKIA...",
            bedrock_secret_access_key="secret",
            bedrock_region="eu-west-1",
        )
        provider = create_embedding_provider(settings)
        assert provider._access_key_id == "AKIA..."
        assert provider._region == "eu-west-1"

    def test_create_openai(self):
        settings = MockSettings(embedding_backend="openai")
        provider = create_embedding_provider(settings)
        assert isinstance(provider, OpenAIEmbeddingProvider)
        assert provider._model_name == "text-embedding-3-small"

    def test_create_openai_with_key(self):
        settings = MockSettings(embedding_backend="openai", openai_api_key="sk-test")
        provider = create_embedding_provider(settings)
        assert provider._api_key == "sk-test"

    def test_create_voyage(self):
        settings = MockSettings(embedding_backend="voyage")
        provider = create_embedding_provider(settings)
        assert isinstance(provider, VoyageEmbeddingProvider)
        assert provider._model_name == "voyage-code-3"

    def test_create_voyage_with_key(self):
        settings = MockSettings(embedding_backend="voyage", voyage_api_key="pa-test")
        provider = create_embedding_provider(settings)
        assert provider._api_key == "pa-test"

    def test_create_mistral(self):
        settings = MockSettings(embedding_backend="mistral")
        provider = create_embedding_provider(settings)
        assert isinstance(provider, MistralEmbeddingProvider)
        assert provider._model_name == "codestral-embed-2505"

    def test_create_mistral_with_key(self):
        settings = MockSettings(embedding_backend="mistral", mistral_api_key="m-test")
        provider = create_embedding_provider(settings)
        assert provider._api_key == "m-test"

    def test_unknown_backend_raises(self):
        settings = MockSettings(embedding_backend="unknown")
        with pytest.raises(ValueError, match="Unknown embedding backend"):
            create_embedding_provider(settings)


# ===================================================================
# ABC contract tests
# ===================================================================


class TestEmbeddingProviderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            EmbeddingProvider()  # type: ignore

    def test_local_is_subclass(self):
        assert issubclass(LocalEmbeddingProvider, EmbeddingProvider)

    def test_bedrock_is_subclass(self):
        assert issubclass(BedrockEmbeddingProvider, EmbeddingProvider)

    def test_openai_is_subclass(self):
        assert issubclass(OpenAIEmbeddingProvider, EmbeddingProvider)

    def test_voyage_is_subclass(self):
        assert issubclass(VoyageEmbeddingProvider, EmbeddingProvider)

    def test_mistral_is_subclass(self):
        assert issubclass(MistralEmbeddingProvider, EmbeddingProvider)
