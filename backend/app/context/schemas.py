"""Pydantic schemas for the context enrichment / code explanation API."""
from typing import List, Optional

from pydantic import BaseModel, Field


class ExplainRichRequest(BaseModel):
    """Pre-assembled prompt from the extension's 8-stage pipeline.

    Used by POST /context/explain-rich. The extension performs LSP resolution,
    ranked file gathering, semantic search, and XML assembly before sending
    the complete prompt here for direct LLM forwarding.
    """
    assembled_prompt: str = Field(..., description="Complete XML prompt ready for LLM")
    snippet: str = Field(..., description="Original selected code (for logging)")
    file_path: str = Field(..., description="Workspace-relative path (for logging)")
    line_start: int = Field(..., ge=1)
    line_end: int = Field(..., ge=1)
    language: str = Field(default="")
    workspace_id: Optional[str] = Field(
        default=None,
        description="Workspace ID for RAG search; RAG is skipped when absent",
    )


class StructuredExplanation(BaseModel):
    """Typed fields parsed from the LLM's JSON response."""
    purpose: str = ""
    inputs: str = ""
    outputs: str = ""
    business_context: str = ""
    dependencies: str = ""
    gotchas: str = ""


class ExplainResponse(BaseModel):
    """Response for POST /context/explain-rich."""
    explanation: str
    model: str
    language: str
    file_path: str
    line_start: int
    line_end: int
    structured: Optional[StructuredExplanation] = None
