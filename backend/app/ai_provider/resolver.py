"""Provider resolver for AI provider selection.

This module provides a service that resolves which AI provider to use
based on configuration and health checks.

Usage:
    from app.ai_provider.resolver import ProviderResolver
    from app.config import get_config

    config = get_config()
    resolver = ProviderResolver(config.summary)
    resolver.resolve()

    status = resolver.get_status()
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.config import SummaryConfig

from .base import AIProvider
from .claude_bedrock import ClaudeBedrockProvider
from .claude_direct import ClaudeDirectProvider

logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    """Status of a single provider."""
    name: str
    healthy: bool


@dataclass
class AIStatus:
    """Overall AI status response."""
    summary_enabled: bool
    active_provider: Optional[str]
    providers: List[ProviderStatus]


class ProviderResolver:
    """Resolves and manages AI providers based on configuration.

    This service reads API keys from config, creates providers for those
    with non-empty keys, performs health checks, and sets the active provider.

    Provider priority order: claude_bedrock first, then claude_direct.

    Attributes:
        config: Summary configuration with enabled flag and API keys.
        active_provider: The currently active provider (if any).
        provider_statuses: Health status of all configured providers.
    """

    # Provider priority order (first healthy provider wins)
    PROVIDER_PRIORITY = ["claude_bedrock", "claude_direct"]

    def __init__(self, config: SummaryConfig) -> None:
        """Initialize the provider resolver.

        Args:
            config: Summary configuration with enabled flag and API keys.
        """
        self.config = config
        self.active_provider: Optional[AIProvider] = None
        self.active_provider_name: Optional[str] = None
        self.provider_statuses: Dict[str, bool] = {}
        self._providers: Dict[str, AIProvider] = {}

    def _get_api_key(self, provider_name: str) -> str:
        """Get the API key for a provider from config.

        Args:
            provider_name: Provider name (claude_bedrock, claude_direct).

        Returns:
            API key string (empty if not configured).
        """
        if provider_name == "claude_bedrock":
            return self.config.claude_bedrock_api_key
        elif provider_name == "claude_direct":
            return self.config.claude_direct_api_key
        return ""

    def _create_provider(self, name: str, api_key: str) -> Optional[AIProvider]:
        """Create a provider instance by name with the given API key.

        Args:
            name: Provider name (claude_bedrock, claude_direct).
            api_key: API key for the provider.

        Returns:
            AIProvider instance or None if creation fails.
        """
        if name in self._providers:
            return self._providers[name]

        try:
            if name == "claude_direct":
                provider = ClaudeDirectProvider(api_key=api_key)
            elif name == "claude_bedrock":
                # Parse AWS credentials from api_key
                # Format: "ACCESS_KEY:SECRET_KEY" or "ACCESS_KEY:SECRET_KEY:SESSION_TOKEN"
                # Use split with maxsplit=2 to preserve any colons in session token
                parts = api_key.split(":", 2)
                if len(parts) < 2:
                    logger.warning(f"Invalid claude_bedrock_api_key format. Expected ACCESS_KEY:SECRET_KEY")
                    return None
                aws_access_key_id = parts[0]
                aws_secret_access_key = parts[1]
                aws_session_token = parts[2] if len(parts) > 2 else None
                provider = ClaudeBedrockProvider(
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    aws_session_token=aws_session_token,
                )
            else:
                logger.warning(f"Unknown provider: {name}")
                return None

            self._providers[name] = provider
            return provider
        except Exception as e:
            logger.error(f"Failed to create provider {name}: {e}")
            return None

    def resolve(self) -> Optional[AIProvider]:
        """Resolve the active provider based on config and health checks.

        Iterates through PROVIDER_PRIORITY, skips providers with empty API keys,
        runs health_check() on providers with keys, and sets the first healthy
        provider as active.

        Returns:
            The active AIProvider or None if no healthy provider found.
        """
        if not self.config.enabled:
            logger.info("Summary is disabled, skipping provider resolution")
            return None

        logger.info(f"Resolving AI provider from priority: {self.PROVIDER_PRIORITY}")

        for provider_name in self.PROVIDER_PRIORITY:
            api_key = self._get_api_key(provider_name)

            # Skip providers with empty API keys
            if not api_key:
                logger.info(f"⏭️ Provider {provider_name} skipped (no API key configured)")
                continue

            provider = self._create_provider(provider_name, api_key)
            if provider is None:
                self.provider_statuses[provider_name] = False
                continue

            try:
                healthy = provider.health_check()
                self.provider_statuses[provider_name] = healthy

                if healthy:
                    logger.info(f"✅ Provider {provider_name} is healthy, setting as active")
                    self.active_provider = provider
                    self.active_provider_name = provider_name
                    return provider
                else:
                    logger.warning(f"⚠️ Provider {provider_name} health check failed")
            except Exception as e:
                logger.error(f"❌ Provider {provider_name} health check error: {e}")
                self.provider_statuses[provider_name] = False

        logger.warning("No healthy AI provider found")
        return None

    def get_status(self) -> AIStatus:
        """Get the current AI status.

        Returns:
            AIStatus with summary_enabled, active_provider, and provider statuses.
        """
        providers = [
            ProviderStatus(name=name, healthy=healthy)
            for name, healthy in self.provider_statuses.items()
        ]

        return AIStatus(
            summary_enabled=self.config.enabled,
            active_provider=self.active_provider_name,
            providers=providers,
        )

    def get_active_provider(self) -> Optional[AIProvider]:
        """Get the currently active provider.

        Returns:
            The active AIProvider or None.
        """
        return self.active_provider


# Global resolver instance (initialized on startup)
_resolver: Optional[ProviderResolver] = None


def get_resolver() -> Optional[ProviderResolver]:
    """Get the global provider resolver instance."""
    return _resolver


def set_resolver(resolver: ProviderResolver) -> None:
    """Set the global provider resolver instance."""
    global _resolver
    _resolver = resolver

