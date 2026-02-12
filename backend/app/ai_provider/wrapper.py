"""Reusable wrapper for calling the active AI provider.

This module provides high-level functions for calling the AI provider
with proper error handling, timeout management, and logging.

Usage:
    from app.ai_provider.wrapper import call_summary, call_code_prompt

    # For summarization
    result = call_summary(chat_messages)

    # For code prompt generation (no AI call, just template)
    result = call_code_prompt(decision_summary, context_snippet)
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

from fastapi import HTTPException

from .base import ChatMessage, DecisionSummary
from .prompts import get_code_prompt
from .resolver import get_resolver

logger = logging.getLogger(__name__)

# Default timeout for AI provider calls (in seconds)
DEFAULT_TIMEOUT_SECONDS = 60


@dataclass
class ProviderCallResult:
    """Result of an AI provider call.

    Attributes:
        success: Whether the call succeeded.
        provider_name: Name of the provider used.
        data: The result data (DecisionSummary or str).
        error: Error message if call failed.
    """
    success: bool
    provider_name: Optional[str]
    data: Optional[any] = None
    error: Optional[str] = None


class AIProviderError(Exception):
    """Base exception for AI provider errors."""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ProviderNotAvailableError(AIProviderError):
    """Raised when no AI provider is available."""
    def __init__(self, message: str = "No active AI provider available"):
        super().__init__(message, status_code=503)


class ProviderCallError(AIProviderError):
    """Raised when an AI provider call fails."""
    def __init__(self, message: str, provider_name: str):
        self.provider_name = provider_name
        super().__init__(f"Provider {provider_name} error: {message}", status_code=500)


class JSONParseError(AIProviderError):
    """Raised when AI response JSON parsing fails."""
    def __init__(self, message: str, provider_name: str):
        self.provider_name = provider_name
        super().__init__(
            f"Failed to parse AI response as JSON from {provider_name}: {message}",
            status_code=500
        )


def _get_active_provider():
    """Get the active AI provider with proper error handling.

    Returns:
        Tuple of (provider, provider_name, resolver).

    Raises:
        ProviderNotAvailableError: If no provider is available.
    """
    resolver = get_resolver()

    if resolver is None:
        logger.warning("AI provider call failed: resolver not initialized")
        raise ProviderNotAvailableError(
            "AI summarization service is not initialized. Please check server configuration."
        )

    if not resolver.config.enabled:
        logger.info("AI provider call rejected: summary feature is disabled")
        raise ProviderNotAvailableError(
            "AI summarization is not enabled in configuration."
        )

    provider = resolver.get_active_provider()
    provider_name = resolver.active_provider_name

    if provider is None:
        # Log provider status for debugging
        status = resolver.get_status()
        provider_info = ", ".join(
            [f"{p.name}={'healthy' if p.healthy else 'unhealthy'}" for p in status.providers]
        ) or "no providers configured"
        logger.warning(f"AI provider call failed: no active provider. Status: {provider_info}")
        raise ProviderNotAvailableError(
            "No active AI provider available. Please check provider configuration and API keys."
        )

    return provider, provider_name, resolver


def call_summary(messages: List[ChatMessage]) -> DecisionSummary:
    """Call the active AI provider to generate a structured summary.

    This function handles:
    - Provider resolution from the global resolver
    - Error handling with appropriate HTTP status codes
    - Logging of requests and responses

    Args:
        messages: List of ChatMessage objects to summarize.

    Returns:
        DecisionSummary with structured summary data.

    Raises:
        ProviderNotAvailableError: If no provider is available (503).
        JSONParseError: If AI response parsing fails (500).
        ProviderCallError: If the provider call fails (500).
    """
    provider, provider_name, _ = _get_active_provider()

    logger.info(f"Calling summary with provider: {provider_name}, messages: {len(messages)}")

    try:
        summary = provider.summarize_structured(messages)
        logger.info(f"Successfully generated summary with provider: {provider_name}")
        return summary

    except ValueError as e:
        # ValueError is raised when JSON parsing fails in the provider
        error_msg = str(e)
        logger.error(f"JSON parsing error from provider {provider_name}: {error_msg}")
        raise JSONParseError(error_msg, provider_name)

    except Exception as e:
        # Catch-all for other provider errors (API errors, network issues, etc.)
        error_msg = str(e)
        logger.error(f"Provider {provider_name} error during summarization: {error_msg}")
        raise ProviderCallError(error_msg, provider_name)


def call_code_prompt(
    problem_statement: str,
    proposed_solution: str,
    affected_components: List[str],
    risk_level: str,
    context_snippet: Optional[str] = None,
) -> str:
    """Generate a code prompt from decision summary components.

    This function does not call an AI provider - it constructs a prompt
    using the template that can be used with code generation tools.

    Args:
        problem_statement: Description of the problem to solve.
        proposed_solution: The proposed solution approach.
        affected_components: List of components/files affected.
        risk_level: Risk assessment (low/medium/high).
        context_snippet: Optional code snippet for context.

    Returns:
        str: Formatted code prompt for code generation.
    """
    logger.info(f"Generating code prompt for {len(affected_components)} components")

    code_prompt = get_code_prompt(
        problem_statement=problem_statement,
        proposed_solution=proposed_solution,
        affected_components=affected_components,
        risk_level=risk_level,
        context_snippet=context_snippet,
    )

    logger.debug(f"Generated code prompt with {len(code_prompt)} characters")
    return code_prompt


def handle_provider_error(error: AIProviderError) -> HTTPException:
    """Convert an AIProviderError to an HTTPException.

    Args:
        error: The AIProviderError to convert.

    Returns:
        HTTPException with appropriate status code and detail.
    """
    return HTTPException(
        status_code=error.status_code,
        detail=error.message,
    )


def call_summary_http(messages: List[ChatMessage]) -> DecisionSummary:
    """Call summary with automatic HTTP exception conversion.

    Convenience wrapper that catches AIProviderError and converts
    to HTTPException for use in FastAPI endpoints.

    Args:
        messages: List of ChatMessage objects to summarize.

    Returns:
        DecisionSummary with structured summary data.

    Raises:
        HTTPException: On any provider error.
    """
    try:
        return call_summary(messages)
    except AIProviderError as e:
        raise handle_provider_error(e)

