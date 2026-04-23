"""Unit tests for the Phase 9.16 forked-agent primitive.

Covers:
- fork_call sends a tools=[] chat_with_tools call to the provided
  provider with the supplied system + user message
- fork_call returns the response text on success
- fork_call returns "" on empty user_message (refuses to call)
- fork_call returns "" on provider exception (caller treats as unclear)
- fork_call returns "" on empty response text
- build_pr_context_prefix assembles title + description + diff blocks
- build_pr_context_prefix truncates at the byte budget with a marker
- build_pr_context_prefix omits sections when inputs are empty
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agent_loop.forked import build_pr_context_prefix, fork_call

# ---------------------------------------------------------------------------
# build_pr_context_prefix
# ---------------------------------------------------------------------------


class TestBuildPRContextPrefix:
    def test_full_input_renders_all_sections(self):
        out = build_pr_context_prefix(
            pr_title="Add OAuth PKCE support",
            pr_description="Implements PKCE for the public clients.",
            file_diffs_text="### `src/auth.py`\n```diff\n+x\n```",
            impact_graph="src/auth.py ← used by 3 files",
        )
        assert "## PR title" in out
        assert "Add OAuth PKCE support" in out
        assert "## PR description" in out
        assert "Implements PKCE" in out
        assert "## PR diff" in out
        assert "src/auth.py" in out
        assert "## Impact graph" in out

    def test_omits_empty_sections(self):
        """Empty title or description shouldn't produce orphan headers."""
        out = build_pr_context_prefix(
            pr_title="",
            pr_description="",
            file_diffs_text="### `x.py`\n```diff\n+y\n```",
        )
        assert "## PR title" not in out
        assert "## PR description" not in out
        assert "## PR diff" in out
        # Impact graph is also optional
        assert "## Impact graph" not in out

    def test_diff_truncated_at_budget(self):
        big_diff = "x" * 50_000
        out = build_pr_context_prefix(
            pr_title="t",
            pr_description="d",
            file_diffs_text=big_diff,
            diff_budget_chars=10_000,
        )
        assert "PR diff truncated at 10000 chars" in out
        # The diff section after truncation marker is bounded
        diff_section = out.split("## PR diff", 1)[1]
        assert len(diff_section) < 11_000  # marker adds ~50 chars

    def test_empty_diff_omits_diff_section(self):
        out = build_pr_context_prefix(
            pr_title="t",
            pr_description="d",
            file_diffs_text="",
        )
        assert "## PR diff" not in out
        assert "## PR title" in out


# ---------------------------------------------------------------------------
# fork_call
# ---------------------------------------------------------------------------


def _make_provider(text_out: str = "OK", raises=None):
    """Fake AIProvider with a chat_with_tools attribute."""
    provider = MagicMock()
    response = MagicMock()
    response.text = text_out
    if raises is not None:
        provider.chat_with_tools.side_effect = raises
    else:
        provider.chat_with_tools.return_value = response
    return provider


class TestForkCall:
    @pytest.mark.asyncio
    async def test_happy_path_returns_text(self):
        provider = _make_provider("verdict: confirmed")
        out = await fork_call(
            provider=provider,
            system_prompt="you are a verifier",
            user_message="check finding X",
        )
        assert out == "verdict: confirmed"

    @pytest.mark.asyncio
    async def test_empty_user_message_returns_empty(self):
        provider = _make_provider("OK")
        out = await fork_call(
            provider=provider,
            system_prompt="sys",
            user_message="   ",
        )
        assert out == ""
        provider.chat_with_tools.assert_not_called()

    @pytest.mark.asyncio
    async def test_provider_exception_returns_empty(self):
        provider = _make_provider(raises=RuntimeError("throttled"))
        out = await fork_call(
            provider=provider,
            system_prompt="sys",
            user_message="check finding",
        )
        assert out == ""

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self):
        provider = _make_provider("")
        out = await fork_call(
            provider=provider,
            system_prompt="sys",
            user_message="check finding",
        )
        assert out == ""

    @pytest.mark.asyncio
    async def test_call_uses_no_tools(self):
        """fork_call must pass tools=[] to chat_with_tools."""
        provider = _make_provider("OK")
        await fork_call(
            provider=provider,
            system_prompt="sys",
            user_message="check finding",
        )
        # positional: (messages, tools, max_tokens, system)
        positional = provider.chat_with_tools.call_args.args
        assert positional[1] == []  # tools

    @pytest.mark.asyncio
    async def test_call_passes_system_and_user_message(self):
        provider = _make_provider("OK")
        await fork_call(
            provider=provider,
            system_prompt="cached system text",
            user_message="varying user text",
        )
        positional = provider.chat_with_tools.call_args.args
        messages = positional[0]
        system = positional[3]
        assert system == "cached system text"
        assert messages == [{"role": "user", "content": [{"text": "varying user text"}]}]

    @pytest.mark.asyncio
    async def test_max_tokens_default_passed(self):
        provider = _make_provider("OK")
        await fork_call(
            provider=provider,
            system_prompt="sys",
            user_message="msg",
        )
        positional = provider.chat_with_tools.call_args.args
        assert positional[2] == 800  # default max_tokens

    @pytest.mark.asyncio
    async def test_max_tokens_override(self):
        provider = _make_provider("OK")
        await fork_call(
            provider=provider,
            system_prompt="sys",
            user_message="msg",
            max_tokens=1500,
        )
        positional = provider.chat_with_tools.call_args.args
        assert positional[2] == 1500
