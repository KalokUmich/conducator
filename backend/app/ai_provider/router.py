"""AI Provider API router.

This module provides the REST API endpoints for AI provider status and summarization.

Endpoints:
    GET /ai/status - Get current AI provider status
    POST /ai/summarize - Summarize messages using the active AI provider
    POST /ai/code-prompt - Generate a code prompt from a decision summary
"""
import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .base import ChatMessage
from .resolver import get_resolver
from .wrapper import (
    AIProviderError,
    call_code_prompt,
    call_summary,
    handle_provider_error,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


class ProviderStatusResponse(BaseModel):
    """Response model for individual provider status."""
    name: str
    healthy: bool


class AIStatusResponse(BaseModel):
    """Response model for GET /ai/status endpoint."""
    summary_enabled: bool
    active_provider: Optional[str]
    providers: List[ProviderStatusResponse]


class MessageInput(BaseModel):
    """Input model for a single chat message."""
    role: Literal["host", "engineer"]
    text: str
    timestamp: float


class SummarizeRequest(BaseModel):
    """Request model for POST /ai/summarize endpoint."""
    messages: List[MessageInput]


class DecisionSummaryResponse(BaseModel):
    """Response model for POST /ai/summarize endpoint - structured decision summary."""
    type: Literal["decision_summary"] = "decision_summary"
    topic: str
    problem_statement: str
    proposed_solution: str
    requires_code_change: bool
    affected_components: List[str]
    risk_level: Literal["low", "medium", "high"]
    next_steps: List[str]


class DecisionSummaryInput(BaseModel):
    """Input model for decision summary in code-prompt request.

    Matches the structure of DecisionSummaryResponse from /ai/summarize.
    """
    type: Literal["decision_summary"] = "decision_summary"
    topic: str
    problem_statement: str
    proposed_solution: str
    requires_code_change: bool
    affected_components: List[str]
    risk_level: Literal["low", "medium", "high"]
    next_steps: List[str]


class CodePromptRequest(BaseModel):
    """Request model for POST /ai/code-prompt endpoint."""
    decision_summary: DecisionSummaryInput
    context_snippet: Optional[str] = None


class CodePromptResponse(BaseModel):
    """Response model for POST /ai/code-prompt endpoint."""
    code_prompt: str


@router.get("/status", response_model=AIStatusResponse)
async def get_ai_status() -> AIStatusResponse:
    """Get the current AI provider status.

    Returns the summary enabled flag, active provider name,
    and health status of all configured providers.

    Returns:
        AIStatusResponse with:
            - summary_enabled: Whether AI summarization is enabled
            - active_provider: Name of the active provider (or null)
            - providers: List of provider statuses with name and healthy flag
    """
    resolver = get_resolver()

    if resolver is None:
        # Resolver not initialized (summary disabled or startup not complete)
        return AIStatusResponse(
            summary_enabled=False,
            active_provider=None,
            providers=[],
        )

    status = resolver.get_status()

    return AIStatusResponse(
        summary_enabled=status.summary_enabled,
        active_provider=status.active_provider,
        providers=[
            ProviderStatusResponse(name=p.name, healthy=p.healthy)
            for p in status.providers
        ],
    )


@router.post("/summarize", response_model=DecisionSummaryResponse)
async def summarize_messages(request: SummarizeRequest) -> DecisionSummaryResponse:
    """Summarize messages using the active AI provider.

    Uses the reusable wrapper to call the AI provider with proper
    error handling, timeout management, and logging.

    Args:
        request: SummarizeRequest with list of messages to summarize.

    Returns:
        DecisionSummaryResponse with structured summary including:
            - type: Always "decision_summary"
            - topic: Brief topic of the discussion
            - problem_statement: Description of the problem
            - proposed_solution: The proposed solution
            - requires_code_change: Whether code changes are needed
            - affected_components: List of affected components
            - risk_level: Risk assessment (low/medium/high)
            - next_steps: List of action items

    Raises:
        HTTPException 503: If summary is disabled or no active provider available.
        HTTPException 500: If the provider fails to generate summary or JSON parsing fails.
    """
    # Convert request messages to ChatMessage objects for the provider
    chat_messages = [
        ChatMessage(role=msg.role, text=msg.text, timestamp=msg.timestamp)
        for msg in request.messages
    ]

    try:
        # Use the wrapper to call the provider
        summary = call_summary(chat_messages)

        return DecisionSummaryResponse(
            type=summary.type,
            topic=summary.topic,
            problem_statement=summary.problem_statement,
            proposed_solution=summary.proposed_solution,
            requires_code_change=summary.requires_code_change,
            affected_components=summary.affected_components,
            risk_level=summary.risk_level,
            next_steps=summary.next_steps,
        )

    except AIProviderError as e:
        raise handle_provider_error(e)


@router.post("/code-prompt", response_model=CodePromptResponse)
async def generate_code_prompt(request: CodePromptRequest) -> CodePromptResponse:
    """Generate a code prompt from a decision summary.

    Takes a decision summary (typically from /ai/summarize) and constructs
    a prompt suitable for code generation models to produce unified diff output.

    Uses the reusable wrapper for consistent logging. This endpoint does not
    call an AI provider - it simply constructs the prompt using a template.
    The resulting prompt can be used with code generation tools like Codex SDK
    or other code agents.

    Args:
        request: CodePromptRequest with:
            - decision_summary: The structured decision summary from /ai/summarize
            - context_snippet: Optional code snippet for additional context

    Returns:
        CodePromptResponse with:
            - code_prompt: A formatted prompt string for code generation

    Example:
        Request:
        {
            "decision_summary": {
                "type": "decision_summary",
                "topic": "Add user authentication",
                "problem_statement": "Users cannot log in securely",
                "proposed_solution": "Implement JWT-based authentication",
                "requires_code_change": true,
                "affected_components": ["auth/login.py", "auth/middleware.py"],
                "risk_level": "medium",
                "next_steps": ["Implement login endpoint", "Add JWT validation"]
            },
            "context_snippet": "def login(username, password):\\n    pass"
        }

        Response:
        {
            "code_prompt": "You are a senior software engineer..."
        }
    """
    summary = request.decision_summary

    logger.info(f"Generating code prompt for topic: {summary.topic}")

    # Use the wrapper for consistent logging and potential future enhancements
    code_prompt_str = call_code_prompt(
        problem_statement=summary.problem_statement,
        proposed_solution=summary.proposed_solution,
        affected_components=summary.affected_components,
        risk_level=summary.risk_level,
        context_snippet=request.context_snippet,
    )

    return CodePromptResponse(code_prompt=code_prompt_str)