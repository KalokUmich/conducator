"""Tests for AIProvider interface and implementations."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.ai_provider import (
    AIProvider,
    ChatMessage,
    ClaudeBedrockProvider,
    ClaudeDirectProvider,
    DecisionSummary,
)


class TestAIProviderInterface:
    """Tests for the AIProvider abstract interface."""

    def test_cannot_instantiate_abstract_class(self):
        """AIProvider should not be directly instantiable."""
        with pytest.raises(TypeError):
            AIProvider()

    def test_interface_has_required_methods(self):
        """AIProvider should define health_check, summarize, and summarize_structured methods."""
        assert hasattr(AIProvider, "health_check")
        assert hasattr(AIProvider, "summarize")
        assert hasattr(AIProvider, "summarize_structured")


class TestClaudeDirectProvider:
    """Tests for ClaudeDirectProvider implementation."""

    def test_initialization_with_defaults(self):
        """Test provider initialization with default values."""
        provider = ClaudeDirectProvider(api_key="test-key")
        assert provider.api_key == "test-key"
        assert provider.model == ClaudeDirectProvider.DEFAULT_MODEL
        assert provider.base_url == ClaudeDirectProvider.DEFAULT_BASE_URL

    def test_initialization_with_custom_values(self):
        """Test provider initialization with custom values."""
        provider = ClaudeDirectProvider(
            api_key="custom-key",
            model="claude-3-opus-20240229",
            base_url="https://custom.api.com"
        )
        assert provider.api_key == "custom-key"
        assert provider.model == "claude-3-opus-20240229"
        assert provider.base_url == "https://custom.api.com"

    def test_implements_ai_provider_interface(self):
        """ClaudeDirectProvider should implement AIProvider interface."""
        provider = ClaudeDirectProvider(api_key="test-key")
        assert isinstance(provider, AIProvider)

    def test_health_check_success(self):
        """Test health_check returns True when API is accessible."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = ClaudeDirectProvider(api_key="test-key")
            result = provider.health_check()

            assert result is True
            mock_client.messages.create.assert_called_once()

    def test_health_check_failure(self):
        """Test health_check returns False when API call fails."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API Error")

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = ClaudeDirectProvider(api_key="test-key")
            result = provider.health_check()

            assert result is False

    def test_summarize_success(self):
        """Test summarize returns expected summary."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="This is a summary.")]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = ClaudeDirectProvider(api_key="test-key")
            result = provider.summarize(["Hello", "World"])

            assert result == "This is a summary."
            mock_client.messages.create.assert_called_once()

    def test_summarize_empty_messages(self):
        """Test summarize returns empty string for empty messages."""
        provider = ClaudeDirectProvider(api_key="test-key")
        result = provider.summarize([])

        assert result == ""

    def test_get_client_raises_import_error(self):
        """Test _get_client raises ImportError when anthropic not installed."""
        provider = ClaudeDirectProvider(api_key="test-key")
        with patch.dict("sys.modules", {"anthropic": None}):
            # Force reimport to trigger ImportError
            provider._client = None
            with pytest.raises(ImportError, match="anthropic package is required"):
                provider._get_client()

    def test_summarize_structured_success(self):
        """Test summarize_structured returns DecisionSummary with valid JSON response."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        # Mock a valid JSON response
        json_response = json.dumps({
            "type": "decision_summary",
            "topic": "API refactoring",
            "problem_statement": "Current API is slow",
            "proposed_solution": "Add caching layer",
            "requires_code_change": True,
            "affected_components": ["api.py", "cache.py"],
            "risk_level": "medium",
            "next_steps": ["Review PR", "Run tests"]
        })
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json_response)]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = ClaudeDirectProvider(api_key="test-key")
            messages = [
                ChatMessage(role="host", text="The API is slow", timestamp=1234567890),
                ChatMessage(role="engineer", text="Let's add caching", timestamp=1234567891),
            ]
            result = provider.summarize_structured(messages)

            assert isinstance(result, DecisionSummary)
            assert result.type == "decision_summary"
            assert result.topic == "API refactoring"
            assert result.problem_statement == "Current API is slow"
            assert result.proposed_solution == "Add caching layer"
            assert result.requires_code_change is True
            assert result.affected_components == ["api.py", "cache.py"]
            assert result.risk_level == "medium"
            assert result.next_steps == ["Review PR", "Run tests"]

    def test_summarize_structured_empty_messages(self):
        """Test summarize_structured returns empty DecisionSummary for empty messages."""
        provider = ClaudeDirectProvider(api_key="test-key")
        result = provider.summarize_structured([])

        assert isinstance(result, DecisionSummary)
        assert result.type == "decision_summary"
        assert result.topic == ""
        assert result.requires_code_change is False

    def test_summarize_structured_invalid_json(self):
        """Test summarize_structured raises ValueError for invalid JSON response."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        # Mock an invalid JSON response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="This is not JSON")]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = ClaudeDirectProvider(api_key="test-key")
            messages = [ChatMessage(role="host", text="Hello", timestamp=1234567890)]

            with pytest.raises(ValueError, match="Invalid JSON response"):
                provider.summarize_structured(messages)


class TestClaudeBedrockProvider:
    """Tests for ClaudeBedrockProvider implementation."""

    def test_initialization_with_defaults(self):
        """Test provider initialization with default values."""
        provider = ClaudeBedrockProvider()
        assert provider.aws_access_key_id is None
        assert provider.aws_secret_access_key is None
        assert provider.region_name == ClaudeBedrockProvider.DEFAULT_REGION
        assert provider.model_id == ClaudeBedrockProvider.DEFAULT_MODEL_ID

    def test_initialization_with_custom_values(self):
        """Test provider initialization with custom values."""
        provider = ClaudeBedrockProvider(
            aws_access_key_id="AKIATEST",
            aws_secret_access_key="secret",
            aws_session_token="token",
            region_name="eu-west-1",
            model_id="anthropic.claude-3-opus-20240229-v1:0"
        )
        assert provider.aws_access_key_id == "AKIATEST"
        assert provider.aws_secret_access_key == "secret"
        assert provider.aws_session_token == "token"
        assert provider.region_name == "eu-west-1"
        assert provider.model_id == "anthropic.claude-3-opus-20240229-v1:0"

    def test_implements_ai_provider_interface(self):
        """ClaudeBedrockProvider should implement AIProvider interface."""
        provider = ClaudeBedrockProvider()
        assert isinstance(provider, AIProvider)

    def test_health_check_success(self):
        """Test health_check returns True when Bedrock is accessible."""
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.return_value = {"body": MagicMock()}

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = ClaudeBedrockProvider()
            result = provider.health_check()

            assert result is True
            mock_client.invoke_model.assert_called_once()

    def test_health_check_failure(self):
        """Test health_check returns False when Bedrock call fails."""
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.side_effect = Exception("Bedrock Error")

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = ClaudeBedrockProvider()
            result = provider.health_check()

            assert result is False

    def test_summarize_success(self):
        """Test summarize returns expected summary from Bedrock."""
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "content": [{"text": "Bedrock summary result."}]
        })
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = ClaudeBedrockProvider()
            result = provider.summarize(["Message 1", "Message 2"])

            assert result == "Bedrock summary result."
            mock_client.invoke_model.assert_called_once()

    def test_summarize_empty_messages(self):
        """Test summarize returns empty string for empty messages."""
        provider = ClaudeBedrockProvider()
        result = provider.summarize([])

        assert result == ""

    def test_get_client_raises_import_error(self):
        """Test _get_client raises ImportError when boto3 not installed."""
        provider = ClaudeBedrockProvider()
        with patch.dict("sys.modules", {"boto3": None}):
            provider._client = None
            with pytest.raises(ImportError, match="boto3 package is required"):
                provider._get_client()

    def test_client_uses_credentials_when_provided(self):
        """Test that AWS credentials are passed to boto3 client."""
        mock_boto3 = MagicMock()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = ClaudeBedrockProvider(
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="secret123",
                aws_session_token="token456",
                region_name="us-west-2"
            )
            provider._get_client()

            mock_boto3.client.assert_called_once_with(
                "bedrock-runtime",
                region_name="us-west-2",
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="secret123",
                aws_session_token="token456"
            )

    def test_summarize_structured_success(self):
        """Test summarize_structured returns DecisionSummary with valid JSON response."""
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        # Mock a valid JSON response
        json_response = json.dumps({
            "type": "decision_summary",
            "topic": "Database migration",
            "problem_statement": "Need to migrate to PostgreSQL",
            "proposed_solution": "Use Alembic for migrations",
            "requires_code_change": True,
            "affected_components": ["models.py", "migrations/"],
            "risk_level": "high",
            "next_steps": ["Backup data", "Test migration"]
        })
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "content": [{"text": json_response}]
        })
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = ClaudeBedrockProvider()
            messages = [
                ChatMessage(role="host", text="We need to migrate DB", timestamp=1234567890),
                ChatMessage(role="engineer", text="Let's use Alembic", timestamp=1234567891),
            ]
            result = provider.summarize_structured(messages)

            assert isinstance(result, DecisionSummary)
            assert result.type == "decision_summary"
            assert result.topic == "Database migration"
            assert result.problem_statement == "Need to migrate to PostgreSQL"
            assert result.proposed_solution == "Use Alembic for migrations"
            assert result.requires_code_change is True
            assert result.affected_components == ["models.py", "migrations/"]
            assert result.risk_level == "high"
            assert result.next_steps == ["Backup data", "Test migration"]

    def test_summarize_structured_empty_messages(self):
        """Test summarize_structured returns empty DecisionSummary for empty messages."""
        provider = ClaudeBedrockProvider()
        result = provider.summarize_structured([])

        assert isinstance(result, DecisionSummary)
        assert result.type == "decision_summary"
        assert result.topic == ""
        assert result.requires_code_change is False

    def test_summarize_structured_invalid_json(self):
        """Test summarize_structured raises ValueError for invalid JSON response."""
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        # Mock an invalid JSON response
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "content": [{"text": "Not valid JSON at all"}]
        })
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = ClaudeBedrockProvider()
            messages = [ChatMessage(role="host", text="Hello", timestamp=1234567890)]

            with pytest.raises(ValueError, match="Invalid JSON response"):
                provider.summarize_structured(messages)


class TestAllProvidersImplementSameInterface:
    """Tests to verify all providers implement the same interface."""

    def test_all_have_health_check_method(self):
        """All providers should have health_check method."""
        direct = ClaudeDirectProvider(api_key="test")
        bedrock = ClaudeBedrockProvider()

        assert callable(getattr(direct, "health_check", None))
        assert callable(getattr(bedrock, "health_check", None))

    def test_all_have_summarize_method(self):
        """All providers should have summarize method."""
        direct = ClaudeDirectProvider(api_key="test")
        bedrock = ClaudeBedrockProvider()

        assert callable(getattr(direct, "summarize", None))
        assert callable(getattr(bedrock, "summarize", None))

    def test_all_have_summarize_structured_method(self):
        """All providers should have summarize_structured method."""
        direct = ClaudeDirectProvider(api_key="test")
        bedrock = ClaudeBedrockProvider()

        assert callable(getattr(direct, "summarize_structured", None))
        assert callable(getattr(bedrock, "summarize_structured", None))

    def test_all_are_instances_of_ai_provider(self):
        """All providers should be instances of AIProvider."""
        direct = ClaudeDirectProvider(api_key="test")
        bedrock = ClaudeBedrockProvider()

        assert isinstance(direct, AIProvider)
        assert isinstance(bedrock, AIProvider)


class TestProviderResolver:
    """Tests for ProviderResolver service."""

    def test_resolve_disabled_returns_none(self):
        """When summary is disabled, resolve should return None."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver

        config = SummaryConfig(enabled=False)
        resolver = ProviderResolver(config)
        result = resolver.resolve()

        assert result is None
        assert resolver.active_provider is None
        assert resolver.active_provider_name is None

    def test_resolve_skips_empty_api_keys(self):
        """Resolver should skip providers with empty API keys."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="",  # Empty - should be skipped
            claude_direct_api_key=""    # Empty - should be skipped
        )

        resolver = ProviderResolver(config)
        result = resolver.resolve()

        # No providers configured, should return None
        assert result is None
        assert resolver.active_provider is None

    def test_resolve_first_healthy_provider_bedrock(self):
        """Resolver should select first healthy provider (bedrock has priority)."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123",
            claude_direct_api_key="sk-ant-test"
        )

        # Mock bedrock to be healthy
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.return_value = {"body": MagicMock()}

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            resolver = ProviderResolver(config)
            result = resolver.resolve()

            assert result is not None
            assert resolver.active_provider_name == "claude_bedrock"

    def test_resolve_falls_back_to_direct_provider(self):
        """Resolver should fall back to claude_direct if bedrock is unhealthy."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123",
            claude_direct_api_key="sk-ant-test"
        )

        # Mock bedrock to be unhealthy, direct to be healthy
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.side_effect = Exception("Bedrock error")

        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"boto3": mock_boto3, "anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            result = resolver.resolve()

            assert result is not None
            assert resolver.active_provider_name == "claude_direct"
            assert resolver.provider_statuses.get("claude_bedrock") is False
            assert resolver.provider_statuses.get("claude_direct") is True

    def test_resolve_no_healthy_provider(self):
        """Resolver should return None when no providers are healthy."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123",
            claude_direct_api_key=""  # Not configured
        )

        # Mock bedrock to be unhealthy
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.side_effect = Exception("Bedrock error")

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            resolver = ProviderResolver(config)
            result = resolver.resolve()

            assert result is None
            assert resolver.active_provider is None

    def test_get_status_returns_correct_structure(self):
        """get_status should return AIStatus with correct data."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123"
        )

        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.return_value = {"body": MagicMock()}

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            status = resolver.get_status()

            assert status.summary_enabled is True
            assert status.active_provider == "claude_bedrock"
            assert len(status.providers) == 1
            assert status.providers[0].name == "claude_bedrock"
            assert status.providers[0].healthy is True

    def test_bedrock_api_key_with_session_token(self):
        """Resolver should parse session token from bedrock API key."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123:session456"
        )

        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.return_value = {"body": MagicMock()}

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            resolver = ProviderResolver(config)
            resolver.resolve()

            # Verify boto3 was called with session token
            mock_boto3.client.assert_called_once()
            call_kwargs = mock_boto3.client.call_args[1]
            assert call_kwargs["aws_access_key_id"] == "AKIATEST"
            assert call_kwargs["aws_secret_access_key"] == "secret123"
            assert call_kwargs["aws_session_token"] == "session456"

    def test_invalid_bedrock_api_key_format(self):
        """Resolver should handle invalid bedrock API key format."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="invalid-format-no-colon"
        )

        resolver = ProviderResolver(config)
        result = resolver.resolve()

        # Invalid format should result in no provider
        assert result is None
        assert resolver.provider_statuses.get("claude_bedrock") is False


class TestAIStatusEndpoint:
    """Tests for GET /ai/status endpoint."""

    def test_status_when_resolver_not_initialized(self):
        """Endpoint should return disabled status when resolver not set."""
        from fastapi.testclient import TestClient
        from app.ai_provider.resolver import set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        # Create a test app with just the AI router
        test_app = FastAPI()
        test_app.include_router(router)

        # Ensure resolver is not set
        set_resolver(None)

        client = TestClient(test_app)
        response = client.get("/ai/status")

        assert response.status_code == 200
        data = response.json()
        assert data["summary_enabled"] is False
        assert data["active_provider"] is None
        assert data["providers"] == []

    def test_status_with_active_provider(self):
        """Endpoint should return correct status with active provider."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        # Set up resolver with mocked healthy bedrock provider
        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123"
        )

        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.return_value = {"body": MagicMock()}

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            client = TestClient(test_app)
            response = client.get("/ai/status")

            assert response.status_code == 200
            data = response.json()
            assert data["summary_enabled"] is True
            assert data["active_provider"] == "claude_bedrock"
            assert len(data["providers"]) == 1
            assert data["providers"][0]["name"] == "claude_bedrock"
            assert data["providers"][0]["healthy"] is True

        # Clean up
        set_resolver(None)

    def test_status_with_no_healthy_provider(self):
        """Endpoint should show null active_provider when none healthy."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123"
        )

        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.side_effect = Exception("Bedrock error")

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            client = TestClient(test_app)
            response = client.get("/ai/status")

            assert response.status_code == 200
            data = response.json()
            assert data["summary_enabled"] is True
            assert data["active_provider"] is None
            assert len(data["providers"]) == 1
            assert data["providers"][0]["healthy"] is False

        # Clean up
        set_resolver(None)


class TestSummarizeEndpoint:
    """Tests for POST /ai/summarize endpoint."""

    def test_summarize_returns_503_when_resolver_not_initialized(self):
        """Endpoint should return 503 when resolver is not set."""
        from fastapi.testclient import TestClient
        from app.ai_provider.resolver import set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        # Ensure resolver is not set
        set_resolver(None)

        client = TestClient(test_app)
        response = client.post("/ai/summarize", json={
            "messages": [{"role": "host", "text": "Hello", "timestamp": 1234567890}]
        })

        assert response.status_code == 503
        assert "not initialized" in response.json()["detail"]

    def test_summarize_returns_503_when_summary_disabled(self):
        """Endpoint should return 503 when summary is disabled."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        # Set up resolver with summary disabled
        config = SummaryConfig(enabled=False)
        resolver = ProviderResolver(config)
        set_resolver(resolver)

        client = TestClient(test_app)
        response = client.post("/ai/summarize", json={
            "messages": [{"role": "host", "text": "Hello", "timestamp": 1234567890}]
        })

        assert response.status_code == 503
        assert "not enabled" in response.json()["detail"]

        # Clean up
        set_resolver(None)

    def test_summarize_returns_503_when_no_active_provider(self):
        """Endpoint should return 503 when no active provider available."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123"
        )

        # All providers fail health check
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.side_effect = Exception("Bedrock error")

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            client = TestClient(test_app)
            response = client.post("/ai/summarize", json={
                "messages": [{"role": "host", "text": "Hello", "timestamp": 1234567890}]
            })

            assert response.status_code == 503
            assert "No active AI provider" in response.json()["detail"]

        # Clean up
        set_resolver(None)

    def test_summarize_success(self):
        """Endpoint should return structured summary when provider is available."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(
            enabled=True,
            claude_direct_api_key="sk-ant-test"
        )

        # Mock anthropic module for health check
        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            # Mock summarize_structured to return a DecisionSummary
            mock_summary = DecisionSummary(
                type="decision_summary",
                topic="Test topic",
                problem_statement="Test problem",
                proposed_solution="Test solution",
                requires_code_change=True,
                affected_components=["file1.py", "file2.py"],
                risk_level="medium",
                next_steps=["Step 1", "Step 2"]
            )
            with patch.object(
                resolver.active_provider,
                "summarize_structured",
                return_value=mock_summary
            ):
                client = TestClient(test_app)
                response = client.post(
                    "/ai/summarize",
                    json={
                        "messages": [
                            {"role": "host", "text": "Hello", "timestamp": 1234567890},
                            {"role": "engineer", "text": "World", "timestamp": 1234567891}
                        ]
                    }
                )

                assert response.status_code == 200
                data = response.json()
                # Verify all required JSON keys exist
                assert data["type"] == "decision_summary"
                assert data["topic"] == "Test topic"
                assert data["problem_statement"] == "Test problem"
                assert data["proposed_solution"] == "Test solution"
                assert data["requires_code_change"] is True
                assert data["affected_components"] == ["file1.py", "file2.py"]
                assert data["risk_level"] == "medium"
                assert data["next_steps"] == ["Step 1", "Step 2"]

        # Clean up
        set_resolver(None)

    def test_summarize_returns_500_on_provider_error(self):
        """Endpoint should return 500 when provider raises an error."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(
            enabled=True,
            claude_direct_api_key="sk-ant-test"
        )

        # Mock anthropic module for health check
        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

        # Mock summarize_structured to raise an error
        with patch.object(
            resolver.active_provider,
            "summarize_structured",
            side_effect=RuntimeError("Provider error")
        ):
            client = TestClient(test_app)
            response = client.post(
                "/ai/summarize",
                json={
                    "messages": [{"role": "host", "text": "Hello", "timestamp": 1234567890}]
                }
            )

            assert response.status_code == 500
            detail = response.json()["detail"]
            # The wrapper format includes "Provider <name> error: <message>"
            assert "Provider" in detail or "error" in detail.lower()

        # Clean up
        set_resolver(None)

    def test_summarize_empty_messages(self):
        """Endpoint should handle empty messages list with default DecisionSummary."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(
            enabled=True,
            claude_direct_api_key="sk-ant-test"
        )

        # Mock anthropic module for health check
        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            client = TestClient(test_app)
            response = client.post("/ai/summarize", json={"messages": []})

            assert response.status_code == 200
            data = response.json()
            # Empty messages should return default DecisionSummary
            assert data["type"] == "decision_summary"
            assert data["topic"] == ""
            assert data["requires_code_change"] is False

        # Clean up
        set_resolver(None)

    def test_summarize_returns_500_on_json_parsing_error(self):
        """Endpoint should return 500 with retry suggestion when JSON parsing fails."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(
            enabled=True,
            claude_direct_api_key="sk-ant-test"
        )

        # Mock anthropic module for health check
        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

        # Mock summarize_structured to raise ValueError (JSON parsing error)
        with patch.object(
            resolver.active_provider,
            "summarize_structured",
            side_effect=ValueError("Invalid JSON response from AI: Expecting value")
        ):
            client = TestClient(test_app)
            response = client.post(
                "/ai/summarize",
                json={
                    "messages": [{"role": "host", "text": "Hello", "timestamp": 1234567890}]
                }
            )

            assert response.status_code == 500
            detail = response.json()["detail"]
            # Should mention JSON parsing failure
            assert "JSON" in detail
            # The wrapper format includes provider name and error details
            assert "claude_direct" in detail or "parse" in detail.lower()

        # Clean up
        set_resolver(None)


class TestCodePromptEndpoint:
    """Tests for POST /ai/code-prompt endpoint."""

    def test_code_prompt_success(self):
        """Endpoint should return a code prompt from decision summary."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        client = TestClient(test_app)
        response = client.post("/ai/code-prompt", json={
            "decision_summary": {
                "type": "decision_summary",
                "topic": "Add user authentication",
                "problem_statement": "Users cannot log in securely",
                "proposed_solution": "Implement JWT-based authentication",
                "requires_code_change": True,
                "affected_components": ["auth/login.py", "auth/middleware.py"],
                "risk_level": "medium",
                "next_steps": ["Implement login endpoint", "Add JWT validation"]
            }
        })

        assert response.status_code == 200
        data = response.json()
        assert "code_prompt" in data
        assert "Users cannot log in securely" in data["code_prompt"]
        assert "JWT-based authentication" in data["code_prompt"]
        assert "auth/login.py" in data["code_prompt"]
        assert "auth/middleware.py" in data["code_prompt"]
        assert "medium" in data["code_prompt"]

    def test_code_prompt_with_context_snippet(self):
        """Endpoint should include context snippet in the prompt."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        client = TestClient(test_app)
        response = client.post("/ai/code-prompt", json={
            "decision_summary": {
                "type": "decision_summary",
                "topic": "Fix login bug",
                "problem_statement": "Login fails silently",
                "proposed_solution": "Add error handling",
                "requires_code_change": True,
                "affected_components": ["auth/login.py"],
                "risk_level": "low",
                "next_steps": ["Add try-catch"]
            },
            "context_snippet": "def login(username, password):\n    pass"
        })

        assert response.status_code == 200
        data = response.json()
        assert "code_prompt" in data
        assert "def login(username, password)" in data["code_prompt"]
        assert "Context" in data["code_prompt"]

    def test_code_prompt_empty_components(self):
        """Endpoint should handle empty affected_components list."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        client = TestClient(test_app)
        response = client.post("/ai/code-prompt", json={
            "decision_summary": {
                "type": "decision_summary",
                "topic": "General improvement",
                "problem_statement": "Code needs refactoring",
                "proposed_solution": "Refactor the module",
                "requires_code_change": True,
                "affected_components": [],
                "risk_level": "low",
                "next_steps": []
            }
        })

        assert response.status_code == 200
        data = response.json()
        assert "code_prompt" in data
        assert "No specific components identified" in data["code_prompt"]

    def test_code_prompt_invalid_request_missing_summary(self):
        """Endpoint should return 422 for missing decision_summary."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        client = TestClient(test_app)
        response = client.post("/ai/code-prompt", json={})

        assert response.status_code == 422

    def test_code_prompt_invalid_risk_level(self):
        """Endpoint should return 422 for invalid risk_level."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        client = TestClient(test_app)
        response = client.post("/ai/code-prompt", json={
            "decision_summary": {
                "type": "decision_summary",
                "topic": "Test",
                "problem_statement": "Test problem",
                "proposed_solution": "Test solution",
                "requires_code_change": True,
                "affected_components": [],
                "risk_level": "invalid_level",  # Invalid value
                "next_steps": []
            }
        })

        assert response.status_code == 422


class TestGetCodePromptFunction:
    """Tests for the get_code_prompt function in prompts.py."""

    def test_get_code_prompt_basic(self):
        """Function should generate a code prompt with all fields."""
        from app.ai_provider.prompts import get_code_prompt

        prompt = get_code_prompt(
            problem_statement="Users cannot log in",
            proposed_solution="Add authentication",
            affected_components=["auth.py", "login.py"],
            risk_level="medium"
        )

        assert "Users cannot log in" in prompt
        assert "Add authentication" in prompt
        assert "auth.py" in prompt
        assert "login.py" in prompt
        assert "medium" in prompt
        assert "unified diff" in prompt.lower()

    def test_get_code_prompt_with_context(self):
        """Function should include context snippet when provided."""
        from app.ai_provider.prompts import get_code_prompt

        prompt = get_code_prompt(
            problem_statement="Bug in login",
            proposed_solution="Fix the bug",
            affected_components=["login.py"],
            risk_level="low",
            context_snippet="def login():\n    return None"
        )

        assert "def login():" in prompt
        assert "Context" in prompt

    def test_get_code_prompt_empty_components(self):
        """Function should handle empty components list."""
        from app.ai_provider.prompts import get_code_prompt

        prompt = get_code_prompt(
            problem_statement="Test",
            proposed_solution="Test",
            affected_components=[],
            risk_level="low"
        )

        assert "No specific components identified" in prompt

    def test_get_code_prompt_none_values(self):
        """Function should handle None values gracefully."""
        from app.ai_provider.prompts import get_code_prompt

        prompt = get_code_prompt(
            problem_statement=None,
            proposed_solution=None,
            affected_components=None,
            risk_level=None
        )

        assert "No problem statement provided" in prompt
        assert "No solution proposed" in prompt
        assert "No specific components identified" in prompt
        assert "unknown" in prompt


class TestAIProviderWrapper:
    """Tests for the AI provider wrapper functions."""

    def test_call_summary_raises_when_resolver_not_initialized(self):
        """call_summary should raise ProviderNotAvailableError when resolver is None."""
        from app.ai_provider.wrapper import call_summary, ProviderNotAvailableError
        from app.ai_provider.resolver import set_resolver

        # Ensure resolver is not set
        set_resolver(None)

        with pytest.raises(ProviderNotAvailableError) as exc_info:
            call_summary([])

        assert "not initialized" in str(exc_info.value.message)
        assert exc_info.value.status_code == 503

    def test_call_summary_raises_when_disabled(self):
        """call_summary should raise ProviderNotAvailableError when summary is disabled."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.wrapper import call_summary, ProviderNotAvailableError

        config = SummaryConfig(enabled=False)
        resolver = ProviderResolver(config)
        set_resolver(resolver)

        with pytest.raises(ProviderNotAvailableError) as exc_info:
            call_summary([])

        assert "not enabled" in str(exc_info.value.message)
        assert exc_info.value.status_code == 503

        # Cleanup
        set_resolver(None)

    def test_call_summary_raises_when_no_provider_available(self):
        """call_summary should raise ProviderNotAvailableError when no provider is configured."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.wrapper import call_summary, ProviderNotAvailableError

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="",
            claude_direct_api_key=""
        )
        resolver = ProviderResolver(config)
        resolver.resolve()  # Will find no providers
        set_resolver(resolver)

        with pytest.raises(ProviderNotAvailableError) as exc_info:
            call_summary([])

        assert "No active AI provider" in str(exc_info.value.message)
        assert exc_info.value.status_code == 503

        # Cleanup
        set_resolver(None)

    def test_call_summary_success_with_mock_provider(self):
        """call_summary should return DecisionSummary on success."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.wrapper import call_summary
        from app.ai_provider import ChatMessage, DecisionSummary

        config = SummaryConfig(
            enabled=True,
            claude_direct_api_key="test-key"
        )

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"topic": "test", "problem_statement": "prob", "proposed_solution": "sol", "requires_code_change": false, "affected_components": [], "risk_level": "low", "next_steps": []}')]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            messages = [ChatMessage(role="host", text="Hello", timestamp=1234567890)]
            result = call_summary(messages)

            assert isinstance(result, DecisionSummary)
            assert result.topic == "test"

        # Cleanup
        set_resolver(None)

    def test_call_summary_raises_json_parse_error(self):
        """call_summary should raise JSONParseError when response is invalid JSON."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.wrapper import call_summary, JSONParseError
        from app.ai_provider import ChatMessage

        config = SummaryConfig(
            enabled=True,
            claude_direct_api_key="test-key"
        )

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='not valid json')]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            messages = [ChatMessage(role="host", text="Hello", timestamp=1234567890)]

            with pytest.raises(JSONParseError) as exc_info:
                call_summary(messages)

            assert exc_info.value.status_code == 500
            assert "claude_direct" in exc_info.value.provider_name

        # Cleanup
        set_resolver(None)

    def test_call_summary_raises_provider_call_error_on_exception(self):
        """call_summary should raise ProviderCallError on general exceptions."""
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.wrapper import call_summary, ProviderCallError
        from app.ai_provider import ChatMessage

        config = SummaryConfig(
            enabled=True,
            claude_direct_api_key="test-key"
        )

        # Mock successful health check response
        health_check_response = MagicMock()
        health_check_response.content = [MagicMock(text="OK")]

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        # First call succeeds (health check), second fails (summarization)
        mock_client.messages.create.side_effect = [
            health_check_response,
            Exception("API error")
        ]

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            messages = [ChatMessage(role="host", text="Hello", timestamp=1234567890)]

            with pytest.raises(ProviderCallError) as exc_info:
                call_summary(messages)

            assert exc_info.value.status_code == 500
            assert "API error" in str(exc_info.value.message)

        # Cleanup
        set_resolver(None)

    def test_call_code_prompt_returns_string(self):
        """call_code_prompt should return a formatted string."""
        from app.ai_provider.wrapper import call_code_prompt

        result = call_code_prompt(
            problem_statement="Test problem",
            proposed_solution="Test solution",
            affected_components=["file1.py", "file2.py"],
            risk_level="medium",
            context_snippet="def test(): pass"
        )

        assert isinstance(result, str)
        assert "Test problem" in result
        assert "Test solution" in result
        assert "file1.py" in result
        assert "file2.py" in result
        assert "medium" in result
        assert "def test(): pass" in result

    def test_call_code_prompt_handles_empty_components(self):
        """call_code_prompt should handle empty components list."""
        from app.ai_provider.wrapper import call_code_prompt

        result = call_code_prompt(
            problem_statement="Test problem",
            proposed_solution="Test solution",
            affected_components=[],
            risk_level="low"
        )

        assert isinstance(result, str)
        assert "Test problem" in result

    def test_handle_provider_error_converts_to_http_exception(self):
        """handle_provider_error should convert AIProviderError to HTTPException."""
        from fastapi import HTTPException
        from app.ai_provider.wrapper import (
            handle_provider_error,
            ProviderNotAvailableError,
            ProviderCallError,
            JSONParseError
        )

        # Test ProviderNotAvailableError
        error1 = ProviderNotAvailableError("No provider")
        http_exc1 = handle_provider_error(error1)
        assert isinstance(http_exc1, HTTPException)
        assert http_exc1.status_code == 503
        assert "No provider" in http_exc1.detail

        # Test ProviderCallError
        error2 = ProviderCallError("API failed", "claude_direct")
        http_exc2 = handle_provider_error(error2)
        assert isinstance(http_exc2, HTTPException)
        assert http_exc2.status_code == 500

        # Test JSONParseError
        error3 = JSONParseError("Invalid JSON", "claude_direct")
        http_exc3 = handle_provider_error(error3)
        assert isinstance(http_exc3, HTTPException)
        assert http_exc3.status_code == 500

    def test_call_summary_http_convenience_wrapper(self):
        """call_summary_http should convert exceptions to HTTPException."""
        from app.ai_provider.wrapper import call_summary_http
        from app.ai_provider.resolver import set_resolver
        from fastapi import HTTPException

        # Ensure resolver is not set
        set_resolver(None)

        with pytest.raises(HTTPException) as exc_info:
            call_summary_http([])

        assert exc_info.value.status_code == 503


class TestSummarizeEndpointWithMockProvider:
    """Extended tests for POST /ai/summarize with mock provider scenarios."""

    def test_summarize_with_multiple_message_roles(self):
        """Endpoint should handle messages from different roles (host, engineer, observer)."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(enabled=True, claude_direct_api_key="sk-ant-test")

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            mock_summary = DecisionSummary(
                type="decision_summary",
                topic="Multi-role discussion",
                problem_statement="Complex problem",
                proposed_solution="Team solution",
                requires_code_change=True,
                affected_components=["api.py"],
                risk_level="high",
                next_steps=["Review", "Implement"]
            )
            with patch.object(resolver.active_provider, "summarize_structured", return_value=mock_summary):
                client = TestClient(test_app)
                response = client.post("/ai/summarize", json={
                    "messages": [
                        {"role": "host", "text": "Let's discuss the API", "timestamp": 1000},
                        {"role": "engineer", "text": "I suggest REST", "timestamp": 1001},
                        {"role": "engineer", "text": "Looks good", "timestamp": 1002},
                        {"role": "host", "text": "Agreed", "timestamp": 1003}
                    ]
                })

                assert response.status_code == 200
                data = response.json()
                assert data["topic"] == "Multi-role discussion"
                assert data["risk_level"] == "high"

        set_resolver(None)

    def test_summarize_with_long_messages(self):
        """Endpoint should handle long message content."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(enabled=True, claude_direct_api_key="sk-ant-test")

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            mock_summary = DecisionSummary(
                type="decision_summary",
                topic="Long discussion",
                problem_statement="Detailed problem",
                proposed_solution="Comprehensive solution",
                requires_code_change=False,
                affected_components=[],
                risk_level="low",
                next_steps=[]
            )
            with patch.object(resolver.active_provider, "summarize_structured", return_value=mock_summary):
                client = TestClient(test_app)
                # Create a long message (10KB of text)
                long_text = "This is a detailed technical discussion. " * 250
                response = client.post("/ai/summarize", json={
                    "messages": [
                        {"role": "host", "text": long_text, "timestamp": 1000}
                    ]
                })

                assert response.status_code == 200
                assert response.json()["topic"] == "Long discussion"

        set_resolver(None)

    def test_summarize_fallback_from_bedrock_to_direct(self):
        """Endpoint should work when bedrock fails and falls back to direct."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        # Configure both providers, but bedrock will fail health check
        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123",
            claude_direct_api_key="sk-ant-test"
        )

        # Mock boto3 to fail
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value.invoke_model.side_effect = Exception("Bedrock unavailable")

        # Mock anthropic to succeed
        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"boto3": mock_boto3, "anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            # Should have fallen back to claude_direct
            assert resolver.active_provider_name == "claude_direct"

            mock_summary = DecisionSummary(
                type="decision_summary",
                topic="Fallback test",
                problem_statement="Test",
                proposed_solution="Solution",
                requires_code_change=False,
                affected_components=[],
                risk_level="low",
                next_steps=[]
            )
            with patch.object(resolver.active_provider, "summarize_structured", return_value=mock_summary):
                client = TestClient(test_app)
                response = client.post("/ai/summarize", json={
                    "messages": [{"role": "host", "text": "Test", "timestamp": 1000}]
                })

                assert response.status_code == 200
                assert response.json()["topic"] == "Fallback test"

        set_resolver(None)

    def test_summarize_invalid_message_format(self):
        """Endpoint should return 422 for invalid message format."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        client = TestClient(test_app)

        # Missing required fields
        response = client.post("/ai/summarize", json={
            "messages": [{"role": "host"}]  # Missing text and timestamp
        })
        assert response.status_code == 422

        # Invalid role
        response = client.post("/ai/summarize", json={
            "messages": [{"role": "invalid_role", "text": "Hello", "timestamp": 1000}]
        })
        assert response.status_code == 422

    def test_summarize_with_special_characters(self):
        """Endpoint should handle messages with special characters."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(enabled=True, claude_direct_api_key="sk-ant-test")

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            mock_summary = DecisionSummary(
                type="decision_summary",
                topic="Special chars",
                problem_statement="Test <script>alert('xss')</script>",
                proposed_solution="Sanitize input",
                requires_code_change=True,
                affected_components=["security.py"],
                risk_level="high",
                next_steps=["Review"]
            )
            with patch.object(resolver.active_provider, "summarize_structured", return_value=mock_summary):
                client = TestClient(test_app)
                response = client.post("/ai/summarize", json={
                    "messages": [
                        {"role": "host", "text": "Test <script>alert('xss')</script> & \"quotes\"", "timestamp": 1000},
                        {"role": "engineer", "text": "Unicode: 你好 🎉 émojis", "timestamp": 1001}
                    ]
                })

                assert response.status_code == 200

        set_resolver(None)


class TestCodePromptEndpointWithMockProvider:
    """Extended tests for POST /ai/code-prompt with various scenarios."""

    def test_code_prompt_with_all_risk_levels(self):
        """Endpoint should handle all valid risk levels."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)
        client = TestClient(test_app)

        for risk_level in ["low", "medium", "high"]:
            response = client.post("/ai/code-prompt", json={
                "decision_summary": {
                    "type": "decision_summary",
                    "topic": f"Test {risk_level}",
                    "problem_statement": "Problem",
                    "proposed_solution": "Solution",
                    "requires_code_change": True,
                    "affected_components": ["file.py"],
                    "risk_level": risk_level,
                    "next_steps": []
                }
            })
            assert response.status_code == 200
            assert risk_level in response.json()["code_prompt"]

    def test_code_prompt_with_many_components(self):
        """Endpoint should handle many affected components."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)
        client = TestClient(test_app)

        components = [f"module{i}/file{i}.py" for i in range(20)]
        response = client.post("/ai/code-prompt", json={
            "decision_summary": {
                "type": "decision_summary",
                "topic": "Large refactor",
                "problem_statement": "Need to update many files",
                "proposed_solution": "Batch update",
                "requires_code_change": True,
                "affected_components": components,
                "risk_level": "high",
                "next_steps": []
            }
        })

        assert response.status_code == 200
        prompt = response.json()["code_prompt"]
        # Verify some components are in the prompt
        assert "module0/file0.py" in prompt
        assert "module19/file19.py" in prompt

    def test_code_prompt_with_multiline_context(self):
        """Endpoint should handle multiline context snippets."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)
        client = TestClient(test_app)

        context = """def existing_function():
    # This is the current implementation
    result = []
    for item in items:
        if item.is_valid():
            result.append(item.process())
    return result"""

        response = client.post("/ai/code-prompt", json={
            "decision_summary": {
                "type": "decision_summary",
                "topic": "Refactor function",
                "problem_statement": "Function is slow",
                "proposed_solution": "Use list comprehension",
                "requires_code_change": True,
                "affected_components": ["utils.py"],
                "risk_level": "low",
                "next_steps": []
            },
            "context_snippet": context
        })

        assert response.status_code == 200
        prompt = response.json()["code_prompt"]
        assert "def existing_function" in prompt
        assert "list comprehension" in prompt

    def test_code_prompt_without_code_change_required(self):
        """Endpoint should still work when requires_code_change is False."""
        from fastapi.testclient import TestClient
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)
        client = TestClient(test_app)

        response = client.post("/ai/code-prompt", json={
            "decision_summary": {
                "type": "decision_summary",
                "topic": "Documentation update",
                "problem_statement": "Docs are outdated",
                "proposed_solution": "Update README",
                "requires_code_change": False,
                "affected_components": [],
                "risk_level": "low",
                "next_steps": ["Update docs"]
            }
        })

        assert response.status_code == 200
        assert "code_prompt" in response.json()


class TestAIStatusEndpointHealthChecks:
    """Extended tests for GET /ai/status with various health check scenarios."""

    def test_status_with_mixed_provider_health(self):
        """Endpoint should show correct status when providers have mixed health."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        # Configure both providers
        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123",
            claude_direct_api_key="sk-ant-test"
        )

        # Mock boto3 to fail (bedrock unhealthy)
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value.invoke_model.side_effect = Exception("Bedrock error")

        # Mock anthropic to succeed (direct healthy)
        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"boto3": mock_boto3, "anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            client = TestClient(test_app)
            response = client.get("/ai/status")

            assert response.status_code == 200
            data = response.json()
            assert data["summary_enabled"] is True
            assert data["active_provider"] == "claude_direct"
            assert len(data["providers"]) == 2

            # Find provider statuses
            bedrock_status = next(p for p in data["providers"] if p["name"] == "claude_bedrock")
            direct_status = next(p for p in data["providers"] if p["name"] == "claude_direct")

            assert bedrock_status["healthy"] is False
            assert direct_status["healthy"] is True

        set_resolver(None)

    def test_status_with_bedrock_healthy_first(self):
        """Endpoint should select bedrock when it's healthy (priority order)."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123",
            claude_direct_api_key="sk-ant-test"
        )

        # Mock bedrock to succeed
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value.invoke_model.return_value = {"body": MagicMock()}

        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"boto3": mock_boto3, "anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            client = TestClient(test_app)
            response = client.get("/ai/status")

            assert response.status_code == 200
            data = response.json()
            # Bedrock has priority and is healthy
            assert data["active_provider"] == "claude_bedrock"
            # Resolver stops after finding first healthy provider
            assert len(data["providers"]) >= 1
            # The active provider should be in the list and healthy
            bedrock_status = next((p for p in data["providers"] if p["name"] == "claude_bedrock"), None)
            assert bedrock_status is not None
            assert bedrock_status["healthy"] is True

        set_resolver(None)

    def test_status_with_both_providers_unhealthy(self):
        """Endpoint should show no active provider when all fail health checks."""
        from fastapi.testclient import TestClient
        from app.config import SummaryConfig
        from app.ai_provider.resolver import ProviderResolver, set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        config = SummaryConfig(
            enabled=True,
            claude_bedrock_api_key="AKIATEST:secret123",
            claude_direct_api_key="sk-ant-test"
        )

        # Mock both to fail
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value.invoke_model.side_effect = Exception("Bedrock error")

        mock_anthropic = MagicMock()
        mock_anthropic_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_anthropic_client
        mock_anthropic_client.messages.create.side_effect = Exception("Direct error")

        with patch.dict("sys.modules", {"boto3": mock_boto3, "anthropic": mock_anthropic}):
            resolver = ProviderResolver(config)
            resolver.resolve()
            set_resolver(resolver)

            client = TestClient(test_app)
            response = client.get("/ai/status")

            assert response.status_code == 200
            data = response.json()
            assert data["summary_enabled"] is True
            assert data["active_provider"] is None
            assert len(data["providers"]) == 2
            assert all(p["healthy"] is False for p in data["providers"])

        set_resolver(None)

    def test_status_returns_consistent_structure(self):
        """Endpoint should always return consistent JSON structure."""
        from fastapi.testclient import TestClient
        from app.ai_provider.resolver import set_resolver
        from app.ai_provider.router import router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        # Test with no resolver
        set_resolver(None)
        client = TestClient(test_app)
        response = client.get("/ai/status")

        assert response.status_code == 200
        data = response.json()

        # Verify structure
        assert "summary_enabled" in data
        assert "active_provider" in data
        assert "providers" in data
        assert isinstance(data["summary_enabled"], bool)
        assert isinstance(data["providers"], list)