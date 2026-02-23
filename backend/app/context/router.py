"""Context enrichment router — POST /context/explain-rich."""
import json
import logging
import re
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.ai_provider.resolver import get_resolver
from app.rag.router import get_indexer

from .schemas import ExplainRichRequest, ExplainResponse, StructuredExplanation

# System instruction used for the /explain-rich endpoint.
# Quality-first: rich explanation with structured JSON as the preferred format.
# The parser handles both JSON and plain markdown, so the model can choose.
_EXPLAIN_SYSTEM = (
    "You are a senior software engineer reviewing code for your team. "
    "You will receive code context inside XML tags (<context>, <file>, <question>). "
    "The <context> may include a <project> element with the project name, languages, "
    "frameworks, and directory structure — use this to give project-specific answers. "
    "Answer the question directly and concisely. Always cover these points where relevant:\n"
    "• Purpose — what the code does and why it exists\n"
    "• Inputs / parameters — what data it receives and what constraints apply\n"
    "• Outputs / return values — what it returns or the side-effects it produces\n"
    "• Business context — the real-world scenario or domain this code operates in\n"
    "• Key dependencies — external services, injected objects, or patterns relied upon\n"
    "• Gotchas — error paths, edge cases, or non-obvious behaviour worth knowing\n\n"
    "Be specific, not generic. Avoid restating the code verbatim. "
    "You may use **bold** for emphasis, `backticks` for inline code references, "
    "and - bullet lists. Aim for 5–10 sentences total.\n\n"
    "PREFERRED FORMAT: Return a JSON object with these keys so the UI can render "
    "labeled sections. Use empty string for fields that do not apply:\n"
    '{"purpose": "...", "inputs": "...", "outputs": "...", '
    '"business_context": "...", "dependencies": "...", "gotchas": "..."}\n'
    "If you cannot produce valid JSON, a plain markdown response is acceptable."
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/context", tags=["context"])


@router.post("/explain-rich", response_model=ExplainResponse)
async def explain_rich(request: ExplainRichRequest) -> ExplainResponse:
    """Forward a pre-assembled prompt directly to the LLM.

    The extension's 8-stage pipeline (LSP, semantic search, ranked files,
    XML assembly) has already built the complete prompt.  This endpoint
    resolves a healthy AI provider, optionally augments the prompt with
    RAG context, and forwards it.

    Args:
        request: ExplainRichRequest with the assembled XML prompt.

    Returns:
        ExplainResponse with the AI explanation.
    """
    # --- Diagnostic: parse the XML prompt structure ---
    context_breakdown = _analyse_prompt_structure(request.assembled_prompt)
    logger.info(
        "[context/explain-rich] Received: file=%s lines=%d-%d lang=%s "
        "prompt_len=%d workspace_id=%s | %s",
        request.file_path, request.line_start, request.line_end,
        request.language, len(request.assembled_prompt),
        request.workspace_id or "NONE",
        context_breakdown,
    )
    logger.debug(
        "[context/explain-rich] Prompt preview (first 500 chars):\n%s",
        request.assembled_prompt[:500],
    )

    resolver = get_resolver()
    if resolver is None:
        logger.warning("[context/explain-rich] AI resolver is None — no providers configured")
        return JSONResponse(
            {"error": "AI provider not available"},
            status_code=503,
        )

    provider = resolver.get_active_provider() or resolver.resolve()
    if provider is None:
        logger.warning("[context/explain-rich] No healthy AI provider")
        return JSONResponse(
            {"error": "No healthy AI provider found"},
            status_code=503,
        )

    model_id = getattr(provider, "model_id", "unknown")
    logger.info("[context/explain-rich] Using provider: %s model: %s", type(provider).__name__, model_id)

    # --- RAG augmentation (best-effort) ---
    prompt = request.assembled_prompt
    rag_indexer = get_indexer()
    workspace_id = request.workspace_id
    if rag_indexer and workspace_id:
        rag_section = _fetch_rag_section(
            rag_indexer, workspace_id, request.snippet, request.file_path,
        )
        if rag_section:
            # Insert before the closing </context> or append at the end
            if "</context>" in prompt:
                prompt = prompt.replace(
                    "</context>",
                    f"\n{rag_section}\n</context>",
                    1,
                )
            else:
                prompt = prompt + "\n" + rag_section
            logger.info(
                "[context/explain-rich] RAG context injected: +%d chars, new prompt_len=%d",
                len(rag_section), len(prompt),
            )
        else:
            logger.info("[context/explain-rich] RAG: no relevant results found")
    elif not rag_indexer:
        logger.info("[context/explain-rich] RAG: indexer not available")
    elif not workspace_id:
        logger.info("[context/explain-rich] RAG: no workspace_id provided — skipped")

    try:
        raw = provider.call_model(
            prompt,
            max_tokens=4096,
            system=_EXPLAIN_SYSTEM,
        )
        logger.info(
            "[context/explain-rich] Success: file=%s lines=%d-%d model=%s raw_len=%d",
            request.file_path, request.line_start, request.line_end,
            model_id, len(raw),
        )
        logger.debug(
            "[context/explain-rich] Full LLM response:\n%s",
            raw,
        )

        explanation, structured = _parse_structured_response(raw)

        return ExplainResponse(
            explanation=explanation,
            model=model_id,
            language=request.language,
            file_path=request.file_path,
            line_start=request.line_start,
            line_end=request.line_end,
            structured=structured,
        )
    except Exception as exc:
        logger.exception("[context/explain-rich] Explanation failed: %s", exc)
        return JSONResponse(
            {"error": f"Explanation failed: {exc}"},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIELD_LABELS = {
    "purpose": "Purpose",
    "inputs": "Inputs",
    "outputs": "Outputs",
    "business_context": "Business Context",
    "dependencies": "Dependencies",
    "gotchas": "Gotchas",
}


def _parse_structured_response(raw: str) -> tuple[str, Optional[StructuredExplanation]]:
    """Parse the LLM's raw response into structured fields and markdown fallback.

    Handles three common LLM output shapes:
      1. Clean JSON: ``{"purpose": "...", ...}``
      2. Fenced JSON: ````json\\n{...}\\n````
      3. JSON embedded in prose: ``Here is the analysis:\\n{...}\\nAdditional notes...``

    Returns:
        ``(explanation_markdown, structured)`` — on parse failure the original
        text is returned as-is with ``structured=None``.
    """
    data = _extract_json_object(raw)
    if data is None:
        logger.info("[context/explain-rich] Response is not JSON — returning as raw markdown")
        return (raw.strip(), None)

    try:
        structured = StructuredExplanation(**{
            k: str(data.get(k, "")) for k in StructuredExplanation.model_fields
        })
    except Exception:
        logger.warning("[context/explain-rich] JSON parsed but fields invalid — returning raw")
        return (raw.strip(), None)

    # Build a markdown fallback from the structured fields
    parts: list[str] = []
    for field, label in _FIELD_LABELS.items():
        value = getattr(structured, field, "")
        if value:
            parts.append(f"**{label}** — {value}")
    explanation = "\n\n".join(parts) if parts else raw.strip()

    return (explanation, structured)


def _extract_json_object(raw: str) -> dict | None:
    """Extract the first valid JSON object from *raw*, tolerating surrounding text.

    Strategy (tried in order):
      1. ``json.loads`` on the full stripped text (fast path for clean JSON).
      2. Strip markdown code fences (```json ... ```) and retry.
      3. Scan for the first ``{`` and find its matching ``}`` using a
         brace-depth counter, then parse the substring.  This handles the
         common case where the LLM adds prose before or after the JSON.
    """
    text = raw.strip()

    # --- Strategy 1: full text ---
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # --- Strategy 2: strip markdown fences ---
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl >= 0:
            inner = text[first_nl + 1:]
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[:-3].rstrip()
            try:
                obj = json.loads(inner)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass

    # --- Strategy 3: find first { … } block via brace counting ---
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        return obj
                except (json.JSONDecodeError, ValueError):
                    pass
                break

    return None


def _analyse_prompt_structure(prompt: str) -> str:
    """Parse an XML prompt and return a human-readable breakdown.

    Example output: "current-file=1(423ch) definition=1(1200ch) related=3(4500ch) question=yes"
    """
    parts: list[str] = []

    # Count <file role="..."> tags and their sizes
    file_tags = re.findall(
        r'<file\s+[^>]*role=["\'](\w+)["\'][^>]*>(.*?)</file>',
        prompt, re.DOTALL,
    )
    role_counts: dict[str, int] = {}
    role_chars: dict[str, int] = {}
    for role, content in file_tags:
        role_counts[role] = role_counts.get(role, 0) + 1
        role_chars[role] = role_chars.get(role, 0) + len(content)

    for role in ("current", "definition", "related"):
        count = role_counts.get(role, 0)
        chars = role_chars.get(role, 0)
        if count > 0:
            parts.append(f"{role}={count}({chars}ch)")
        else:
            parts.append(f"{role}=0")

    # Check for project section
    has_project = "<project " in prompt
    if has_project:
        parts.append("project=yes")

    # Check for question section
    has_question = "<question>" in prompt
    parts.append(f"question={'yes' if has_question else 'no'}")

    # Check for RAG section (from previous enrichment)
    has_rag = "<related_workspace_code>" in prompt
    if has_rag:
        parts.append("rag=pre-injected")

    return " | ".join(parts)


def _fetch_rag_section(
    rag_indexer,
    workspace_id: str,
    snippet: str,
    file_path: str,
) -> str | None:
    """Query RAG for related code and return an XML section to inject.

    Returns None if no useful results are found or if an error occurs.
    """
    try:
        results = rag_indexer.search(
            workspace_id=workspace_id,
            query=snippet,
            top_k=5,
        )
        if not results:
            return None

        # Filter out chunks from the same file
        filtered = [r for r in results if r.file_path != file_path]
        if not filtered:
            return None

        MAX_RAG_SNIPPET_CHARS = 1500
        parts: list[str] = []
        for item in filtered[:5]:
            symbol_attr = ""
            if item.symbol_name:
                symbol_attr = f' symbol="{item.symbol_name}" type="{item.symbol_type}"'

            # Include actual code content when available
            if item.content:
                snippet_content = item.content[:MAX_RAG_SNIPPET_CHARS]
                if len(item.content) > MAX_RAG_SNIPPET_CHARS:
                    snippet_content += "\n... [truncated]"
                safe_content = snippet_content.replace("]]>", "]]]]><![CDATA[>")
                inner = f"\n<![CDATA[{safe_content}]]>\n"
            else:
                inner = f"\n(semantic match — lines {item.start_line}–{item.end_line})\n"

            parts.append(
                f'<rag-result file="{item.file_path}" '
                f'lines="{item.start_line}-{item.end_line}" '
                f'score="{item.score:.3f}"'
                f'{symbol_attr} '
                f'language="{item.language}">'
                f"{inner}"
                f"</rag-result>"
            )

        logger.info(
            "[context/explain-rich] RAG found %d relevant chunks (top score=%.3f): %s",
            len(filtered[:5]),
            filtered[0].score,
            ", ".join(f"{r.file_path}:{r.start_line}" for r in filtered[:5]),
        )

        return (
            "<related_workspace_code>\n"
            + "\n".join(parts)
            + "\n</related_workspace_code>"
        )
    except Exception as exc:
        logger.warning("[context/explain-rich] RAG search failed (non-fatal): %s", exc)
        return None
