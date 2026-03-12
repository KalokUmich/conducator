"""Tests for Bedrock tool call repair and schema sanitization."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.ai_provider.base import ToolCall, ToolUseResponse, TokenUsage
from app.ai_provider.claude_bedrock import (
    ClaudeBedrockProvider,
    _extract_kv_pairs,
    _extract_tool_calls_from_text,
    _parse_malformed_name,
    _repair_tool_calls,
    _sanitize_property,
    _sanitize_schema,
)

KNOWN_TOOLS = {
    "grep", "read_file", "list_files", "find_symbol", "find_references",
    "file_outline", "get_dependencies", "get_dependents", "git_log",
    "git_diff", "ast_search", "get_callees", "get_callers",
    "git_blame", "git_show", "find_tests", "test_outline", "trace_variable",
}


# ---------------------------------------------------------------------------
# _sanitize_schema
# ---------------------------------------------------------------------------

class TestSanitizeSchema:

    def test_removes_top_level_title(self):
        schema = {"title": "GrepParams", "type": "object", "properties": {}}
        result = _sanitize_schema(schema)
        assert "title" not in result

    def test_removes_defs(self):
        schema = {"$defs": {"Foo": {}}, "type": "object", "properties": {}}
        result = _sanitize_schema(schema)
        assert "$defs" not in result

    def test_removes_definitions(self):
        schema = {"definitions": {"Bar": {}}, "type": "object", "properties": {}}
        result = _sanitize_schema(schema)
        assert "definitions" not in result

    def test_converts_anyof_optional_to_type(self):
        schema = {
            "type": "object",
            "properties": {
                "path": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "title": "Path",
                    "default": None,
                }
            },
        }
        result = _sanitize_schema(schema)
        prop = result["properties"]["path"]
        assert "anyOf" not in prop
        assert prop["type"] == "string"
        assert "title" not in prop

    def test_preserves_required_fields(self):
        schema = {
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string"},
            },
        }
        result = _sanitize_schema(schema)
        assert result["required"] == ["pattern"]
        assert result["properties"]["pattern"]["type"] == "string"

    def test_removes_per_property_title(self):
        schema = {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "title": "Pattern"},
                "path": {"type": "string", "title": "Path"},
            },
        }
        result = _sanitize_schema(schema)
        for prop in result["properties"].values():
            assert "title" not in prop

    def test_does_not_mutate_original(self):
        original = {
            "title": "Foo",
            "type": "object",
            "properties": {
                "x": {"anyOf": [{"type": "integer"}, {"type": "null"}], "title": "X"},
            },
        }
        import copy
        before = copy.deepcopy(original)
        _sanitize_schema(original)
        assert original == before

    def test_handles_nested_object_properties(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "title": "Config",
                    "properties": {
                        "timeout": {
                            "anyOf": [{"type": "integer"}, {"type": "null"}],
                            "title": "Timeout",
                        }
                    },
                }
            },
        }
        result = _sanitize_schema(schema)
        nested = result["properties"]["config"]["properties"]["timeout"]
        assert nested["type"] == "integer"
        assert "anyOf" not in nested
        assert "title" not in nested

    def test_handles_array_items(self):
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "title": "Tag",
                    },
                }
            },
        }
        result = _sanitize_schema(schema)
        items = result["properties"]["tags"]["items"]
        assert items["type"] == "string"
        assert "anyOf" not in items

    def test_all_null_anyof_becomes_string(self):
        schema = {
            "type": "object",
            "properties": {
                "x": {"anyOf": [{"type": "null"}]},
            },
        }
        result = _sanitize_schema(schema)
        assert result["properties"]["x"]["type"] == "string"

    def test_empty_schema(self):
        result = _sanitize_schema({})
        assert result == {}

    def test_real_pydantic_grep_schema(self):
        """Simulate a real Pydantic v2 schema for the grep tool."""
        schema = {
            "title": "GrepParams",
            "$defs": {},
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string", "title": "Pattern", "description": "Regex pattern"},
                "include_glob": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "title": "Include Glob",
                    "default": None,
                    "description": "Glob filter",
                },
                "path": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "title": "Path",
                    "default": None,
                },
                "max_results": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "title": "Max Results",
                    "default": None,
                },
            },
        }
        result = _sanitize_schema(schema)
        assert "title" not in result
        assert "$defs" not in result
        assert result["properties"]["pattern"]["type"] == "string"
        assert result["properties"]["include_glob"]["type"] == "string"
        assert result["properties"]["path"]["type"] == "string"
        assert result["properties"]["max_results"]["type"] == "integer"
        for prop in result["properties"].values():
            assert "anyOf" not in prop
            assert "title" not in prop


# ---------------------------------------------------------------------------
# _extract_kv_pairs
# ---------------------------------------------------------------------------

class TestExtractKvPairs:

    def test_basic_pairs(self):
        text = 'pattern="render" path="CDE"'
        assert _extract_kv_pairs(text) == {"pattern": "render", "path": "CDE"}

    def test_numeric_value(self):
        text = 'max_results="50"'
        assert _extract_kv_pairs(text) == {"max_results": 50}

    def test_no_pairs(self):
        assert _extract_kv_pairs("just some text") == {}

    def test_with_leading_junk(self):
        text = '" pattern="render|Render" include_glob="*.py"'
        result = _extract_kv_pairs(text)
        assert result["pattern"] == "render|Render"
        assert result["include_glob"] == "*.py"


# ---------------------------------------------------------------------------
# _parse_malformed_name
# ---------------------------------------------------------------------------

class TestParseMalformedName:

    def test_exact_match(self):
        name, params = _parse_malformed_name("grep", KNOWN_TOOLS)
        assert name == "grep"
        assert params == {}

    def test_name_with_params(self):
        name, params = _parse_malformed_name(
            'grep" pattern="render|Render|RENDER" include_glob="*.py" path="CDE',
            KNOWN_TOOLS,
        )
        assert name == "grep"
        assert params["pattern"] == "render|Render|RENDER"
        assert params["include_glob"] == "*.py"
        assert params["path"] == "CDE"

    def test_unknown_tool(self):
        name, params = _parse_malformed_name("unknown_tool", KNOWN_TOOLS)
        assert name is None
        assert params == {}

    def test_read_file_with_params(self):
        name, params = _parse_malformed_name(
            'read_file" path="src/main.py" start_line="1" end_line="50"',
            KNOWN_TOOLS,
        )
        assert name == "read_file"
        assert params["path"] == "src/main.py"
        assert params["start_line"] == 1
        assert params["end_line"] == 50


# ---------------------------------------------------------------------------
# _repair_tool_calls
# ---------------------------------------------------------------------------

class TestRepairToolCalls:

    def test_clean_calls_unchanged(self):
        calls = [ToolCall(id="1", name="grep", input={"pattern": "foo"})]
        result = _repair_tool_calls(calls, KNOWN_TOOLS)
        assert len(result) == 1
        assert result[0].name == "grep"
        assert result[0].input == {"pattern": "foo"}

    def test_repairs_malformed_name(self):
        calls = [ToolCall(
            id="1",
            name='grep" pattern="render|Render|RENDER" include_glob="*.py" path="CDE',
            input={},
        )]
        result = _repair_tool_calls(calls, KNOWN_TOOLS)
        assert len(result) == 1
        assert result[0].name == "grep"
        assert result[0].input["pattern"] == "render|Render|RENDER"
        assert result[0].input["include_glob"] == "*.py"

    def test_merges_existing_input(self):
        calls = [ToolCall(
            id="1",
            name='grep" pattern="render"',
            input={"max_results": 100},
        )]
        result = _repair_tool_calls(calls, KNOWN_TOOLS)
        assert result[0].name == "grep"
        assert result[0].input["pattern"] == "render"
        assert result[0].input["max_results"] == 100

    def test_empty_list(self):
        assert _repair_tool_calls([], KNOWN_TOOLS) == []

    def test_unrepairable_passes_through(self):
        calls = [ToolCall(id="1", name="totally_broken_garbage", input={})]
        result = _repair_tool_calls(calls, KNOWN_TOOLS)
        assert result[0].name == "totally_broken_garbage"

    def test_preserves_id(self):
        calls = [ToolCall(
            id="tooluse_abc123",
            name='find_symbol" name="authenticate"',
            input={},
        )]
        result = _repair_tool_calls(calls, KNOWN_TOOLS)
        assert result[0].id == "tooluse_abc123"
        assert result[0].name == "find_symbol"
        assert result[0].input["name"] == "authenticate"


# ---------------------------------------------------------------------------
# _extract_tool_calls_from_text
# ---------------------------------------------------------------------------

class TestExtractToolCallsFromText:

    def test_json_format(self):
        text = 'I will search for it: {"name": "grep", "arguments": {"pattern": "auth", "path": "src"}}'
        calls = _extract_tool_calls_from_text(text, KNOWN_TOOLS)
        assert len(calls) == 1
        assert calls[0].name == "grep"
        assert calls[0].input["pattern"] == "auth"

    def test_function_call_format(self):
        text = 'Let me search: grep(pattern="auth", path="src")'
        calls = _extract_tool_calls_from_text(text, KNOWN_TOOLS)
        assert len(calls) == 1
        assert calls[0].name == "grep"
        assert calls[0].input["pattern"] == "auth"

    def test_no_tool_calls(self):
        text = "There is no tool call in this text."
        calls = _extract_tool_calls_from_text(text, KNOWN_TOOLS)
        assert calls == []

    def test_empty_text(self):
        assert _extract_tool_calls_from_text("", KNOWN_TOOLS) == []

    def test_empty_tools(self):
        assert _extract_tool_calls_from_text("grep()", set()) == []

    def test_json_with_parameters_key(self):
        text = '{"name": "read_file", "parameters": {"path": "main.py"}}'
        calls = _extract_tool_calls_from_text(text, KNOWN_TOOLS)
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].input["path"] == "main.py"

    def test_json_with_input_key(self):
        text = '{"name": "find_symbol", "input": {"name": "auth"}}'
        calls = _extract_tool_calls_from_text(text, KNOWN_TOOLS)
        assert len(calls) == 1
        assert calls[0].name == "find_symbol"

    def test_unknown_tool_in_json_ignored(self):
        text = '{"name": "unknown_tool", "arguments": {"x": 1}}'
        calls = _extract_tool_calls_from_text(text, KNOWN_TOOLS)
        assert calls == []

    def test_multiple_json_calls(self):
        text = (
            'First: {"name": "grep", "arguments": {"pattern": "foo"}} '
            'Then: {"name": "read_file", "arguments": {"path": "bar.py"}}'
        )
        calls = _extract_tool_calls_from_text(text, KNOWN_TOOLS)
        assert len(calls) == 2
        names = {c.name for c in calls}
        assert names == {"grep", "read_file"}


# ---------------------------------------------------------------------------
# chat_with_tools integration — schema sanitized & repairs wired in
# ---------------------------------------------------------------------------

class TestChatWithToolsIntegration:
    """Test that chat_with_tools sanitizes schemas and repairs tool calls."""

    def _make_provider(self, mock_response):
        """Create a provider with a mocked Bedrock client."""
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.converse.return_value = mock_response
        provider = ClaudeBedrockProvider()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider._get_client()
        provider._client = mock_client
        return provider, mock_client

    SAMPLE_TOOLS = [
        {
            "name": "grep",
            "description": "Search files",
            "input_schema": {
                "title": "GrepParams",
                "type": "object",
                "required": ["pattern"],
                "properties": {
                    "pattern": {"type": "string", "title": "Pattern"},
                    "path": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "title": "Path",
                        "default": None,
                    },
                },
            },
        },
        {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                },
            },
        },
    ]

    def test_schema_sanitized_before_sending(self):
        """Tool schemas should have anyOf/title stripped before being sent to Bedrock."""
        response = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
        }
        provider, mock_client = self._make_provider(response)
        provider.chat_with_tools(
            messages=[{"role": "user", "content": [{"text": "test"}]}],
            tools=self.SAMPLE_TOOLS,
        )
        call_args = mock_client.converse.call_args
        tool_config = call_args.kwargs["toolConfig"]
        grep_schema = tool_config["tools"][0]["toolSpec"]["inputSchema"]["json"]

        # Title should be removed
        assert "title" not in grep_schema
        # anyOf should be converted
        path_prop = grep_schema["properties"]["path"]
        assert "anyOf" not in path_prop
        assert path_prop["type"] == "string"
        # Per-property title removed
        assert "title" not in grep_schema["properties"]["pattern"]

    def test_malformed_tool_calls_repaired(self):
        """Tool calls with params in name field should be repaired."""
        response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "id1",
                                "name": 'grep" pattern="render" path="src"',
                                "input": {},
                            }
                        }
                    ]
                }
            },
            "stopReason": "tool_use",
        }
        provider, _ = self._make_provider(response)
        result = provider.chat_with_tools(
            messages=[{"role": "user", "content": [{"text": "find render"}]}],
            tools=self.SAMPLE_TOOLS,
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "grep"
        assert result.tool_calls[0].input["pattern"] == "render"
        assert result.tool_calls[0].input["path"] == "src"

    def test_text_based_tool_extraction_fallback(self):
        """When no toolUse blocks but text contains tool calls, extract them."""
        response = {
            "output": {
                "message": {
                    "content": [
                        {"text": 'I will search: {"name": "grep", "arguments": {"pattern": "auth"}}'}
                    ]
                }
            },
            "stopReason": "end_turn",
        }
        provider, _ = self._make_provider(response)
        result = provider.chat_with_tools(
            messages=[{"role": "user", "content": [{"text": "find auth"}]}],
            tools=self.SAMPLE_TOOLS,
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "grep"
        assert result.tool_calls[0].input["pattern"] == "auth"
        assert result.stop_reason == "tool_use"

    def test_normal_tool_call_unchanged(self):
        """Clean tool calls from Bedrock should pass through unchanged."""
        response = {
            "output": {
                "message": {
                    "content": [
                        {"text": "Let me search."},
                        {
                            "toolUse": {
                                "toolUseId": "id1",
                                "name": "grep",
                                "input": {"pattern": "auth"},
                            }
                        },
                    ]
                }
            },
            "stopReason": "tool_use",
            "usage": {
                "inputTokens": 100,
                "outputTokens": 50,
                "totalTokens": 150,
            },
        }
        provider, _ = self._make_provider(response)
        result = provider.chat_with_tools(
            messages=[{"role": "user", "content": [{"text": "find auth"}]}],
            tools=self.SAMPLE_TOOLS,
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "grep"
        assert result.tool_calls[0].input == {"pattern": "auth"}
        assert result.text == "Let me search."
        assert result.usage.input_tokens == 100

    def test_no_text_extraction_when_structured_calls_exist(self):
        """Text extraction should NOT run when structured tool calls exist."""
        response = {
            "output": {
                "message": {
                    "content": [
                        {"text": '{"name": "read_file", "arguments": {"path": "x.py"}}'},
                        {
                            "toolUse": {
                                "toolUseId": "id1",
                                "name": "grep",
                                "input": {"pattern": "test"},
                            }
                        },
                    ]
                }
            },
            "stopReason": "tool_use",
        }
        provider, _ = self._make_provider(response)
        result = provider.chat_with_tools(
            messages=[{"role": "user", "content": [{"text": "search"}]}],
            tools=self.SAMPLE_TOOLS,
        )
        # Only the structured tool call should be present
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "grep"

    def test_original_tool_schemas_not_mutated(self):
        """The caller's tool list should not be modified in place."""
        import copy
        tools_before = copy.deepcopy(self.SAMPLE_TOOLS)
        response = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
        }
        provider, _ = self._make_provider(response)
        provider.chat_with_tools(
            messages=[{"role": "user", "content": [{"text": "test"}]}],
            tools=self.SAMPLE_TOOLS,
        )
        assert self.SAMPLE_TOOLS == tools_before
