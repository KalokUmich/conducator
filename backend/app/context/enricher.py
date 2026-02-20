"""ContextEnricher — orchestrates context gathering and LLM explanation.

This class is the central coordinator for the /context/explain endpoint.
It uses CodebaseSkills to fill in any context the extension did not provide,
then builds the prompt and calls the configured AI provider.

Future use: the same enricher can be wired into the CGP (Code Generation
Prompt) pipeline so code-prompt generation also benefits from rich context.
"""
import logging
from typing import Optional

from .schemas import ExplainRequest, ExplainResponse
from .skills import (
    build_explanation_prompt,
    extract_context_window,
    extract_imports,
    find_containing_function,
)

logger = logging.getLogger(__name__)


class ContextEnricher:
    """Enrich a code snippet request and produce an AI explanation.

    Usage::

        from app.ai_provider.resolver import get_resolver

        enricher = ContextEnricher(provider=get_resolver().get_provider())
        response = enricher.explain(request)

    The enricher fills in missing context fields using CodebaseSkills, builds
    a focused prompt, calls the LLM, and returns a structured response.
    """

    def __init__(self, provider) -> None:
        """
        Args:
            provider: An AIProvider instance (ClaudeDirectProvider, etc.)
                      with a ``call_model(prompt: str) -> str`` method.
        """
        self._provider = provider

    def explain(self, request: ExplainRequest) -> ExplainResponse:
        """Explain the code snippet in *request*.

        Steps:
        1. Fill in any missing context using CodebaseSkills.
        2. Assemble the explanation prompt via ``build_explanation_prompt``.
        3. Call the LLM.
        4. Return a structured ExplainResponse.

        Args:
            request: Validated ExplainRequest from the API.

        Returns:
            ExplainResponse with the explanation text and metadata.

        Raises:
            RuntimeError: If the AI provider call fails.
        """
        # --- 1. Fill in missing context ---
        file_content = request.file_content or ""
        language = request.language or "text"

        surrounding_code = request.surrounding_code
        if not surrounding_code and file_content:
            surrounding_code = extract_context_window(
                file_content, request.line_start, request.line_end
            )

        imports = request.imports
        if not imports and file_content:
            imports = extract_imports(file_content, language)

        containing_function = request.containing_function
        if not containing_function and file_content:
            containing_function = find_containing_function(
                file_content, request.line_start, language
            )

        # --- 2. Build prompt ---
        prompt = build_explanation_prompt(
            snippet=request.snippet,
            file_path=request.file_path,
            language=language,
            surrounding_code=surrounding_code,
            imports=imports,
            containing_function=containing_function,
            related_files=[rf.model_dump() for rf in request.related_files],
        )

        logger.debug(
            "[ContextEnricher] Prompt built for %s lines %d-%d (%d chars)",
            request.file_path, request.line_start, request.line_end, len(prompt),
        )

        # --- 3. Call LLM ---
        explanation = self._provider.call_model(prompt)

        return ExplainResponse(
            explanation=explanation.strip(),
            model=getattr(self._provider, "model_id", "unknown"),
            language=language,
            file_path=request.file_path,
            line_start=request.line_start,
            line_end=request.line_end,
        )
