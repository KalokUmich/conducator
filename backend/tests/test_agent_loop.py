"""Tests for the agent loop service and message format conversion."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from app.ai_provider.base import AIProvider, ToolCall, ToolUseResponse
from app.ai_provider.claude_direct import _converse_to_anthropic
from app.ai_provider.openai_provider import _converse_to_openai
from app.agent_loop.service import AgentLoopService, AgentResult
from app.code_tools.tools import invalidate_graph_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "auth.py").write_text(textwrap.dedent("""\
        import jwt

        def authenticate(token: str) -> bool:
            try:
                payload = jwt.decode(token, "secret", algorithms=["HS256"])
                return True
            except jwt.InvalidTokenError:
                return False

        def get_user(token: str) -> dict:
            payload = jwt.decode(token, "secret", algorithms=["HS256"])
            return {"user_id": payload["sub"]}
    """))
    (tmp_path / "app" / "router.py").write_text(textwrap.dedent("""\
        from app.auth import authenticate

        def login_endpoint(request):
            token = request.headers.get("Authorization")
            if authenticate(token):
                return {"status": "ok"}
            return {"status": "unauthorized"}
    """))
    invalidate_graph_cache()
    return tmp_path


class MockProvider(AIProvider):
    """Mock AI provider that returns scripted responses."""

    def __init__(self, responses: List[ToolUseResponse]):
        self._responses = list(responses)
        self._call_count = 0

    def health_check(self) -> bool:
        return True

    def summarize(self, messages):
        return ""

    def summarize_structured(self, messages):
        pass

    def call_model(self, prompt, max_tokens=2048, system=None):
        return ""

    def chat_with_tools(self, messages, tools, max_tokens=4096, system=None):
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        # Default: end turn
        return ToolUseResponse(text="Done.", stop_reason="end_turn")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_direct_answer(self, workspace):
        """Model answers immediately without using tools."""
        provider = MockProvider([
            ToolUseResponse(text="The answer is 42.", stop_reason="end_turn"),
        ])
        agent = AgentLoopService(provider=provider, max_iterations=5)
        result = await agent.run("What is the answer?", str(workspace))

        assert result.answer == "The answer is 42."
        assert result.tool_calls_made == 0
        assert result.iterations == 1
        assert result.error is None

    @pytest.mark.asyncio
    async def test_single_tool_call(self, workspace):
        """Model calls one tool then answers."""
        provider = MockProvider([
            # First response: call grep
            ToolUseResponse(
                text="Let me search for authentication code.",
                tool_calls=[ToolCall(id="tc1", name="grep", input={"pattern": "authenticate"})],
                stop_reason="tool_use",
            ),
            # Second response: answer
            ToolUseResponse(
                text="I found the authenticate function in app/auth.py.",
                stop_reason="end_turn",
            ),
        ])
        agent = AgentLoopService(provider=provider, max_iterations=5)
        result = await agent.run("How does auth work?", str(workspace))

        assert "authenticate" in result.answer
        assert result.tool_calls_made == 1
        assert result.iterations == 2

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self, workspace):
        """Model calls multiple tools across iterations."""
        provider = MockProvider([
            # Iteration 1: grep
            ToolUseResponse(
                text="",
                tool_calls=[ToolCall(id="tc1", name="grep", input={"pattern": "authenticate"})],
                stop_reason="tool_use",
            ),
            # Iteration 2: read the file
            ToolUseResponse(
                text="",
                tool_calls=[ToolCall(id="tc2", name="read_file", input={"path": "app/auth.py"})],
                stop_reason="tool_use",
            ),
            # Iteration 3: answer
            ToolUseResponse(
                text="Authentication uses JWT tokens.",
                stop_reason="end_turn",
            ),
        ])
        agent = AgentLoopService(provider=provider, max_iterations=10)
        result = await agent.run("How does auth work?", str(workspace))

        assert result.tool_calls_made == 2
        assert result.iterations == 3
        assert len(result.context_chunks) == 1  # read_file produces a chunk

    @pytest.mark.asyncio
    async def test_max_iterations_reached(self, workspace):
        """Agent stops after max iterations."""
        # Provider always requests more tools
        responses = [
            ToolUseResponse(
                text="",
                tool_calls=[ToolCall(id=f"tc{i}", name="grep", input={"pattern": f"term{i}"})],
                stop_reason="tool_use",
            )
            for i in range(20)
        ]
        provider = MockProvider(responses)
        agent = AgentLoopService(provider=provider, max_iterations=3)
        result = await agent.run("test", str(workspace))

        assert result.iterations == 3
        assert result.error == "Max iterations reached"

    @pytest.mark.asyncio
    async def test_provider_error(self, workspace):
        """Agent handles provider errors gracefully."""

        class ErrorProvider(MockProvider):
            def chat_with_tools(self, *a, **kw):
                raise RuntimeError("API error")

        provider = ErrorProvider([])
        agent = AgentLoopService(provider=provider, max_iterations=5)
        result = await agent.run("test", str(workspace))

        assert result.error == "API error"
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_tool_error_doesnt_crash(self, workspace):
        """If a tool fails, the error is passed back to the model."""
        provider = MockProvider([
            ToolUseResponse(
                text="",
                tool_calls=[ToolCall(id="tc1", name="read_file", input={"path": "nonexistent.py"})],
                stop_reason="tool_use",
            ),
            ToolUseResponse(
                text="File not found, but I can still answer.",
                stop_reason="end_turn",
            ),
        ])
        agent = AgentLoopService(provider=provider, max_iterations=5)
        result = await agent.run("Read nonexistent file", str(workspace))

        assert result.tool_calls_made == 1
        assert result.error is None
        assert "not found" in result.answer.lower() or result.answer != ""

    @pytest.mark.asyncio
    async def test_find_symbol_tool(self, workspace):
        """Agent can use find_symbol."""
        provider = MockProvider([
            ToolUseResponse(
                text="",
                tool_calls=[ToolCall(id="tc1", name="find_symbol", input={"name": "authenticate"})],
                stop_reason="tool_use",
            ),
            ToolUseResponse(
                text="Found authenticate in auth.py.",
                stop_reason="end_turn",
            ),
        ])
        agent = AgentLoopService(provider=provider, max_iterations=5)
        result = await agent.run("Where is authenticate defined?", str(workspace))

        assert result.tool_calls_made == 1
        assert result.iterations == 2

    @pytest.mark.asyncio
    async def test_multiple_tools_in_one_turn(self, workspace):
        """Model calls two tools in a single turn."""
        provider = MockProvider([
            ToolUseResponse(
                text="",
                tool_calls=[
                    ToolCall(id="tc1", name="grep", input={"pattern": "authenticate"}),
                    ToolCall(id="tc2", name="list_files", input={"directory": "app"}),
                ],
                stop_reason="tool_use",
            ),
            ToolUseResponse(
                text="Found it.",
                stop_reason="end_turn",
            ),
        ])
        agent = AgentLoopService(provider=provider, max_iterations=5)
        result = await agent.run("Find auth", str(workspace))

        assert result.tool_calls_made == 2
        assert result.iterations == 2


# ---------------------------------------------------------------------------
# Message format conversion tests
# ---------------------------------------------------------------------------


class TestConverseToAnthropic:
    """Test Bedrock Converse → Anthropic Messages API format conversion."""

    def test_plain_user_message(self):
        msgs = [{"role": "user", "content": [{"text": "Hello"}]}]
        result = _converse_to_anthropic(msgs)
        assert result == [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]

    def test_string_content_passthrough(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = _converse_to_anthropic(msgs)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_assistant_with_tool_use(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"text": "Let me search."},
                    {"toolUse": {"toolUseId": "tc1", "name": "grep", "input": {"pattern": "auth"}}},
                ],
            }
        ]
        result = _converse_to_anthropic(msgs)
        assert len(result) == 1
        blocks = result[0]["content"]
        assert blocks[0] == {"type": "text", "text": "Let me search."}
        assert blocks[1] == {
            "type": "tool_use",
            "id": "tc1",
            "name": "grep",
            "input": {"pattern": "auth"},
        }

    def test_tool_result(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "tc1",
                            "content": [{"text": '{"matches": []}'}],
                        }
                    }
                ],
            }
        ]
        result = _converse_to_anthropic(msgs)
        assert len(result) == 1
        blocks = result[0]["content"]
        assert blocks[0]["type"] == "tool_result"
        assert blocks[0]["tool_use_id"] == "tc1"
        assert blocks[0]["content"] == '{"matches": []}'

    def test_multiple_tool_results_in_one_message(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tc1", "content": [{"text": "result1"}]}},
                    {"toolResult": {"toolUseId": "tc2", "content": [{"text": "result2"}]}},
                ],
            }
        ]
        result = _converse_to_anthropic(msgs)
        blocks = result[0]["content"]
        assert len(blocks) == 2
        assert blocks[0]["tool_use_id"] == "tc1"
        assert blocks[1]["tool_use_id"] == "tc2"

    def test_full_conversation_round_trip(self):
        """Simulate a full agent loop conversation."""
        msgs = [
            {"role": "user", "content": [{"text": "How does auth work?"}]},
            {
                "role": "assistant",
                "content": [
                    {"text": "Let me search."},
                    {"toolUse": {"toolUseId": "tc1", "name": "grep", "input": {"pattern": "auth"}}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tc1", "content": [{"text": "found in auth.py"}]}},
                ],
            },
        ]
        result = _converse_to_anthropic(msgs)
        assert len(result) == 3
        assert result[0]["content"][0]["type"] == "text"
        assert result[1]["content"][1]["type"] == "tool_use"
        assert result[2]["content"][0]["type"] == "tool_result"


class TestConverseToOpenAI:
    """Test Bedrock Converse → OpenAI Chat Completions format conversion."""

    def test_plain_user_message(self):
        msgs = [{"role": "user", "content": [{"text": "Hello"}]}]
        result = _converse_to_openai(msgs)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_string_content_passthrough(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = _converse_to_openai(msgs)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_assistant_with_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"text": "Let me search."},
                    {"toolUse": {"toolUseId": "tc1", "name": "grep", "input": {"pattern": "auth"}}},
                ],
            }
        ]
        result = _converse_to_openai(msgs)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        assert msg["content"] == "Let me search."
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "tc1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "grep"
        assert json.loads(tc["function"]["arguments"]) == {"pattern": "auth"}

    def test_assistant_no_text_with_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "tc1", "name": "grep", "input": {"pattern": "x"}}},
                ],
            }
        ]
        result = _converse_to_openai(msgs)
        assert result[0]["content"] is None
        assert len(result[0]["tool_calls"]) == 1

    def test_tool_results_become_separate_messages(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tc1", "content": [{"text": "result1"}]}},
                    {"toolResult": {"toolUseId": "tc2", "content": [{"text": "result2"}]}},
                ],
            }
        ]
        result = _converse_to_openai(msgs)
        assert len(result) == 2
        assert result[0] == {"role": "tool", "tool_call_id": "tc1", "content": "result1"}
        assert result[1] == {"role": "tool", "tool_call_id": "tc2", "content": "result2"}

    def test_multiple_tool_calls_in_one_assistant_message(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "tc1", "name": "grep", "input": {"pattern": "a"}}},
                    {"toolUse": {"toolUseId": "tc2", "name": "list_files", "input": {"directory": "."}}},
                ],
            }
        ]
        result = _converse_to_openai(msgs)
        assert len(result) == 1
        assert len(result[0]["tool_calls"]) == 2

    def test_full_conversation_round_trip(self):
        """Simulate a full agent loop conversation."""
        msgs = [
            {"role": "user", "content": [{"text": "How does auth work?"}]},
            {
                "role": "assistant",
                "content": [
                    {"text": "Searching..."},
                    {"toolUse": {"toolUseId": "tc1", "name": "grep", "input": {"pattern": "auth"}}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tc1", "content": [{"text": "found it"}]}},
                ],
            },
        ]
        result = _converse_to_openai(msgs)
        assert len(result) == 3
        # User message → plain content
        assert result[0] == {"role": "user", "content": "How does auth work?"}
        # Assistant → tool_calls
        assert result[1]["role"] == "assistant"
        assert result[1]["tool_calls"][0]["function"]["name"] == "grep"
        # Tool result → role: tool
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "tc1"
