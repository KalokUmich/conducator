"""Agent loop service — drives code navigation via LLM + tools.

The loop sends the user query to the LLM along with tool definitions.
The LLM decides which tools to call, the loop executes them and feeds
results back, repeating until the LLM produces a final answer or the
iteration limit is reached.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.ai_provider.base import AIProvider, ToolUseResponse
from app.code_tools.schemas import TOOL_DEFINITIONS
from app.code_tools.tools import execute_tool

from .prompts import build_system_prompt

logger = logging.getLogger(__name__)


@dataclass
class ContextChunk:
    """A piece of code context collected during the agent loop."""
    file_path: str
    content: str
    start_line: int = 0
    end_line: int = 0
    relevance: str = ""


@dataclass
class AgentResult:
    """Result of an agent loop run."""
    answer: str = ""
    context_chunks: List[ContextChunk] = field(default_factory=list)
    tool_calls_made: int = 0
    iterations: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None


class AgentLoopService:
    """Runs the LLM agent loop for code intelligence queries."""

    def __init__(
        self,
        provider: AIProvider,
        max_iterations: int = 15,
    ) -> None:
        self._provider = provider
        self._max_iterations = max_iterations

    async def run(
        self,
        query: str,
        workspace_path: str,
    ) -> AgentResult:
        """Execute the agent loop.

        Args:
            query:          Natural language question about the codebase.
            workspace_path: Absolute path to the workspace root.

        Returns:
            AgentResult with the answer and collected context.
        """
        start = time.monotonic()
        system = build_system_prompt(workspace_path)
        messages = self._initial_messages(query)
        context_chunks: List[ContextChunk] = []
        total_tool_calls = 0

        for iteration in range(self._max_iterations):
            try:
                response = self._provider.chat_with_tools(
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    max_tokens=4096,
                    system=system,
                )
            except Exception as exc:
                logger.error("Agent LLM call failed at iteration %d: %s", iteration, exc)
                return AgentResult(
                    error=str(exc),
                    tool_calls_made=total_tool_calls,
                    iterations=iteration + 1,
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            # If the model produced a final answer (no tool calls), we're done
            if response.stop_reason == "end_turn" or not response.tool_calls:
                return AgentResult(
                    answer=response.text,
                    context_chunks=context_chunks,
                    tool_calls_made=total_tool_calls,
                    iterations=iteration + 1,
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            # Append the assistant's response to the conversation
            messages.append(self._assistant_message(response))

            # Execute each tool call and add results
            tool_results_content = []
            for tc in response.tool_calls:
                total_tool_calls += 1
                logger.info(
                    "Agent tool call #%d: %s(%s)",
                    total_tool_calls, tc.name, _truncate_json(tc.input),
                )

                result = execute_tool(tc.name, workspace_path, tc.input)

                # Collect read_file results as context chunks
                if tc.name == "read_file" and result.success and result.data:
                    context_chunks.append(ContextChunk(
                        file_path=result.data.get("path", ""),
                        content=result.data.get("content", ""),
                        start_line=tc.input.get("start_line", 0),
                        end_line=tc.input.get("end_line", 0),
                        relevance=query,
                    ))

                tool_results_content.append(
                    self._tool_result_block(tc.id, result)
                )

            messages.append(self._tool_results_message(tool_results_content))

        # Exhausted iterations
        return AgentResult(
            answer=response.text if response else "",
            context_chunks=context_chunks,
            tool_calls_made=total_tool_calls,
            iterations=self._max_iterations,
            duration_ms=(time.monotonic() - start) * 1000,
            error="Max iterations reached",
        )

    # ------------------------------------------------------------------
    # Message formatting — provider-agnostic (Bedrock Converse format)
    #
    # We use the Bedrock Converse message format as the canonical format
    # because it's the most structured. Provider adapters in
    # chat_with_tools() handle any necessary translation.
    # ------------------------------------------------------------------

    @staticmethod
    def _initial_messages(query: str) -> List[Dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [{"text": query}],
            }
        ]

    @staticmethod
    def _assistant_message(response: ToolUseResponse) -> Dict[str, Any]:
        content: List[Dict[str, Any]] = []
        if response.text:
            content.append({"text": response.text})
        for tc in response.tool_calls:
            content.append({
                "toolUse": {
                    "toolUseId": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                }
            })
        return {"role": "assistant", "content": content}

    @staticmethod
    def _tool_result_block(tool_use_id: str, result) -> Dict[str, Any]:
        if result.success:
            text = json.dumps(result.data, default=str)
            # Truncate very large results
            if len(text) > 30_000:
                text = text[:30_000] + "\n... (truncated)"
        else:
            text = f"ERROR: {result.error}"

        return {
            "toolUseId": tool_use_id,
            "content": [{"text": text}],
        }

    @staticmethod
    def _tool_results_message(results: List[Dict]) -> Dict[str, Any]:
        return {
            "role": "user",
            "content": [{"toolResult": r} for r in results],
        }


def _truncate_json(obj: Any, max_len: int = 200) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= max_len else s[:max_len] + "..."
