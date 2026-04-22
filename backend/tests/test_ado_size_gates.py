"""Unit tests for the Azure DevOps PR-size gate messages."""

from __future__ import annotations

from app.integrations.azure_devops.router import (
    _large_pr_skip_message,
    _small_pr_skip_message,
)


class TestSmallPrSkipMessage:
    def test_mentions_actual_line_count_and_floor(self):
        msg = _small_pr_skip_message(changed_lines=9, floor=50)
        assert "9 lines" in msg
        assert "50" in msg

    def test_nudges_human_review(self):
        msg = _small_pr_skip_message(changed_lines=20, floor=50)
        # Author should understand this is deliberate, not a bot failure
        assert "human" in msg.lower()
        assert "skipped" in msg.lower() or "floor" in msg.lower()

    def test_contains_header_badge(self):
        msg = _small_pr_skip_message(changed_lines=20, floor=50)
        assert msg.startswith("## 🤖 Conductor AI Code Review")


class TestLargePrSkipMessage:
    def test_mentions_actual_line_count_and_ceiling(self):
        msg = _large_pr_skip_message(changed_lines=3500, ceiling=2200)
        assert "3500 lines" in msg
        assert "2200" in msg

    def test_recommends_split(self):
        msg = _large_pr_skip_message(changed_lines=3500, ceiling=2200)
        lower = msg.lower()
        assert "split" in lower
        # Should reference stackable / independent commits
        assert "independent" in lower or "stacked" in lower or "concern" in lower

    def test_signals_dedicated_splitter_is_future_work(self):
        msg = _large_pr_skip_message(changed_lines=3500, ceiling=2200)
        # Author should know this isn't "review is broken" — a splitter
        # assistant is the planned replacement
        assert "roadmap" in msg.lower() or "future" in msg.lower()

    def test_contains_header_badge(self):
        msg = _large_pr_skip_message(changed_lines=3500, ceiling=2200)
        assert msg.startswith("## 🤖 Conductor AI Code Review")
