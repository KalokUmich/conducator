"""Query markers — single source of truth for the [query_type:X] convention.

The frontend prefixes user queries with markers like ``[query_type:code_review]``
to signal intent to the Brain LLM. The marker is read by the Brain via prompt
context (not by code), but multiple components reference the same string values:

  * ``extension/webview-ui/src/utils/slashCommands.ts`` — emits markers
  * ``extension/webview-ui/src/contexts/ChatContext.tsx`` — detects plan mode
  * ``backend/app/agent_loop/prompts.py`` — few-shot examples + strategy injection
  * ``config/agents/*.md`` — ``strategy:`` frontmatter

Centralizing the values here lets a typo surface as an enum import error
instead of a silent LLM misroute.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class QueryType(str, Enum):
    """Query type markers emitted by the frontend slash commands."""

    CODE_REVIEW = "code_review"
    ISSUE_TRACKING = "issue_tracking"
    SUMMARY = "summary"
    DIFF = "diff"


MARKER_PATTERN = re.compile(r"^\[query_type:([a-z_]+)\]\s*")


def parse_marker(query: str) -> Optional[QueryType]:
    """Extract a QueryType from the start of a raw user query, if present.

    Returns ``None`` when no marker is present or the marker value is not
    a recognized ``QueryType``. The marker is **not** stripped from the query
    — that's still the caller's choice (Brain reads the marker from the full
    text as a hint).
    """
    match = MARKER_PATTERN.match(query)
    if not match:
        return None
    try:
        return QueryType(match.group(1))
    except ValueError:
        return None


def format_marker(qt: QueryType) -> str:
    """Render a marker prefix string, e.g. ``"[query_type:code_review] "``."""
    return f"[query_type:{qt.value}] "
