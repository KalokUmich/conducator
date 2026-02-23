"""Tests for the context enrichment router — /context/explain-rich."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.context.router import router


@pytest.fixture()
def _context_app():
    """Minimal FastAPI app with only the context router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def _mock_provider():
    """A mock AIProvider whose call_model returns a canned explanation."""
    provider = MagicMock()
    provider.call_model.return_value = "This code processes data by splitting on commas."
    provider.model_id = "test-model-1"
    return provider


# ---------------------------------------------------------------------------
# POST /context/explain-rich
# ---------------------------------------------------------------------------


class TestLegacyExplainRemoved:
    """Verify that the legacy /context/explain endpoint has been removed."""

    def test_explain_endpoint_removed(self, _context_app):
        """POST /context/explain should return 404 or 405 after removal."""
        client = TestClient(_context_app)
        response = client.post("/context/explain", json={
            "room_id": "test",
            "snippet": "x = 1",
            "file_path": "x.py",
            "line_start": 1,
            "line_end": 1,
        })
        assert response.status_code in (404, 405)


class TestExplainRich:
    """Tests for the prompt-through endpoint."""

    def test_forwards_assembled_prompt(self, _context_app, _mock_provider):
        """The assembled_prompt should be forwarded directly to call_model."""
        from app.ai_provider.resolver import set_resolver

        resolver = MagicMock()
        resolver.get_active_provider.return_value = _mock_provider
        resolver.resolve.return_value = _mock_provider
        set_resolver(resolver)

        try:
            client = TestClient(_context_app)
            xml_prompt = (
                "<context>\n"
                "  <current-file path='app.py'>def greet(): ...</current-file>\n"
                "  <definition path='utils.py'>def helper(): ...</definition>\n"
                "  <question>Explain this code.</question>\n"
                "</context>"
            )
            response = client.post("/context/explain-rich", json={
                "assembled_prompt": xml_prompt,
                "snippet": "def greet(): ...",
                "file_path": "app.py",
                "line_start": 1,
                "line_end": 1,
                "language": "python",
            })

            assert response.status_code == 200
            data = response.json()
            assert data["explanation"] == "This code processes data by splitting on commas."

            # Verify the full XML prompt was forwarded and a system prompt added.
            _mock_provider.call_model.assert_called_once_with(
                xml_prompt,
                max_tokens=4096,
                system=_mock_provider.call_model.call_args.kwargs["system"],
            )
            # System prompt should be non-empty.
            assert _mock_provider.call_model.call_args.kwargs["system"]
        finally:
            set_resolver(None)

    def test_returns_model_in_response(self, _context_app, _mock_provider):
        """Response should include the provider's model_id."""
        from app.ai_provider.resolver import set_resolver

        resolver = MagicMock()
        resolver.get_active_provider.return_value = _mock_provider
        resolver.resolve.return_value = _mock_provider
        set_resolver(resolver)

        try:
            client = TestClient(_context_app)
            response = client.post("/context/explain-rich", json={
                "assembled_prompt": "Explain X",
                "snippet": "x = 1",
                "file_path": "x.py",
                "line_start": 1,
                "line_end": 1,
            })

            assert response.status_code == 200
            data = response.json()
            assert data["model"] == "test-model-1"
            assert data["file_path"] == "x.py"
            assert data["line_start"] == 1
            assert data["line_end"] == 1
        finally:
            set_resolver(None)

    def test_returns_503_when_no_provider(self, _context_app):
        """Should return 503 when no healthy AI provider is available."""
        from app.ai_provider.resolver import set_resolver

        resolver = MagicMock()
        resolver.get_active_provider.return_value = None
        resolver.resolve.return_value = None
        set_resolver(resolver)

        try:
            client = TestClient(_context_app)
            response = client.post("/context/explain-rich", json={
                "assembled_prompt": "Explain X",
                "snippet": "x = 1",
                "file_path": "x.py",
                "line_start": 1,
                "line_end": 1,
            })

            assert response.status_code == 503
            assert "No healthy AI provider" in response.json()["error"]
        finally:
            set_resolver(None)

    def test_returns_503_when_resolver_is_none(self, _context_app):
        """Should return 503 when the resolver itself is not initialized."""
        from app.ai_provider.resolver import set_resolver

        set_resolver(None)

        client = TestClient(_context_app)
        response = client.post("/context/explain-rich", json={
            "assembled_prompt": "Explain X",
            "snippet": "x = 1",
            "file_path": "x.py",
            "line_start": 1,
            "line_end": 1,
        })

        assert response.status_code == 503
        assert "not available" in response.json()["error"]

    def test_returns_500_on_llm_error(self, _context_app, _mock_provider):
        """Should return 500 when the LLM call raises."""
        from app.ai_provider.resolver import set_resolver

        _mock_provider.call_model.side_effect = RuntimeError("model overloaded")
        resolver = MagicMock()
        resolver.get_active_provider.return_value = _mock_provider
        resolver.resolve.return_value = _mock_provider
        set_resolver(resolver)

        try:
            client = TestClient(_context_app)
            response = client.post("/context/explain-rich", json={
                "assembled_prompt": "Explain X",
                "snippet": "x = 1",
                "file_path": "x.py",
                "line_start": 1,
                "line_end": 1,
            })

            assert response.status_code == 500
            assert "model overloaded" in response.json()["error"]
        finally:
            set_resolver(None)

    def test_validates_line_start_ge_1(self, _context_app):
        """line_start must be >= 1."""
        from app.ai_provider.resolver import set_resolver
        set_resolver(None)

        client = TestClient(_context_app)
        response = client.post("/context/explain-rich", json={
            "assembled_prompt": "Explain X",
            "snippet": "x = 1",
            "file_path": "x.py",
            "line_start": 0,
            "line_end": 1,
        })

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# RAG content injection
# ---------------------------------------------------------------------------


class TestRagContentInjection:
    """Verify that _fetch_rag_section includes CDATA-wrapped code content."""

    def test_rag_results_contain_cdata_content(self):
        from app.context.router import _fetch_rag_section
        from app.rag.schemas import SearchResultItem

        mock_indexer = MagicMock()
        mock_indexer.search.return_value = [
            SearchResultItem(
                file_path="lib/utils.py",
                start_line=10,
                end_line=25,
                symbol_name="parse_config",
                symbol_type="function",
                content="def parse_config(path):\n    with open(path) as f:\n        return json.load(f)",
                score=0.91,
                language="python",
            ),
        ]

        section = _fetch_rag_section(mock_indexer, "ws1", "code snippet", "other.py")
        assert section is not None
        assert "<![CDATA[" in section
        assert "def parse_config" in section
        assert 'file="lib/utils.py"' in section

    def test_rag_results_without_content_show_placeholder(self):
        from app.context.router import _fetch_rag_section
        from app.rag.schemas import SearchResultItem

        mock_indexer = MagicMock()
        mock_indexer.search.return_value = [
            SearchResultItem(
                file_path="lib/empty.py",
                start_line=1,
                end_line=5,
                content="",
                score=0.80,
                language="python",
            ),
        ]

        section = _fetch_rag_section(mock_indexer, "ws1", "code snippet", "other.py")
        assert section is not None
        assert "(semantic match" in section


# ---------------------------------------------------------------------------
# Structured output parsing
# ---------------------------------------------------------------------------


class TestStructuredOutput:
    """Tests for _parse_structured_response."""

    def test_structured_json_parsed_correctly(self):
        from app.context.router import _parse_structured_response

        raw = '{"purpose": "Adds two numbers.", "inputs": "a, b: integers", "outputs": "Sum", "business_context": "", "dependencies": "", "gotchas": "No overflow check"}'
        explanation, structured = _parse_structured_response(raw)
        assert structured is not None
        assert structured.purpose == "Adds two numbers."
        assert structured.inputs == "a, b: integers"
        assert structured.gotchas == "No overflow check"
        assert "**Purpose**" in explanation
        assert "**Gotchas**" in explanation
        # Empty fields should not appear in the markdown
        assert "**Business Context**" not in explanation

    def test_malformed_json_falls_back_to_raw(self):
        from app.context.router import _parse_structured_response

        raw = "This is a plain text explanation without any JSON."
        explanation, structured = _parse_structured_response(raw)
        assert structured is None
        assert explanation == raw

    def test_json_with_code_fences_stripped(self):
        from app.context.router import _parse_structured_response

        raw = '```json\n{"purpose": "Parses config.", "inputs": "", "outputs": "", "business_context": "", "dependencies": "", "gotchas": ""}\n```'
        explanation, structured = _parse_structured_response(raw)
        assert structured is not None
        assert structured.purpose == "Parses config."

    def test_json_embedded_in_prose_extracted(self):
        """LLMs (especially smaller ones) often wrap JSON in prose text."""
        from app.context.router import _parse_structured_response

        raw = (
            'Here is the analysis:\n\n'
            '{\n'
            '"purpose": "Gets loan action policy state.",\n'
            '"inputs": "LoanActionPolicyStateRequest with loan_id and action_type",\n'
            '"outputs": "LoanActionPolicyStateResponse with allow/deny state",\n'
            '"business_context": "Loan management",\n'
            '"dependencies": "AsyncLoanReader, AsyncPolicyService",\n'
            '"gotchas": "Raises RenderException if loan not found"\n'
            '}\n\n'
            'Example input and output:\n\n'
            '```python\nrequest = LoanActionPolicyStateRequest(loan_id=123)\n```'
        )
        explanation, structured = _parse_structured_response(raw)
        assert structured is not None
        assert structured.purpose == "Gets loan action policy state."
        assert structured.dependencies == "AsyncLoanReader, AsyncPolicyService"
        assert "**Purpose**" in explanation

    def test_json_with_escaped_quotes_in_values(self):
        """JSON with escaped quotes inside string values."""
        from app.context.router import _parse_structured_response

        raw = r'{"purpose": "Calls \"validate\" on input.", "inputs": "", "outputs": "", "business_context": "", "dependencies": "", "gotchas": ""}'
        explanation, structured = _parse_structured_response(raw)
        assert structured is not None
        assert "validate" in structured.purpose

    def test_no_json_object_returns_none(self):
        """Text with braces that don't form valid JSON should fall back."""
        from app.context.router import _parse_structured_response

        raw = "The function uses { and } for scope but this isn't JSON."
        explanation, structured = _parse_structured_response(raw)
        assert structured is None
        assert explanation == raw.strip()

    def test_structured_response_in_explain_rich(self, _context_app, _mock_provider):
        """Full integration: explain-rich should return structured field when LLM returns valid JSON."""
        from app.ai_provider.resolver import set_resolver

        _mock_provider.call_model.return_value = (
            '{"purpose": "Greets user.", "inputs": "name: str", "outputs": "greeting string", '
            '"business_context": "User onboarding", "dependencies": "", "gotchas": ""}'
        )
        resolver = MagicMock()
        resolver.get_active_provider.return_value = _mock_provider
        resolver.resolve.return_value = _mock_provider
        set_resolver(resolver)

        try:
            client = TestClient(_context_app)
            response = client.post("/context/explain-rich", json={
                "assembled_prompt": "Explain X",
                "snippet": "def greet(name): ...",
                "file_path": "app.py",
                "line_start": 1,
                "line_end": 1,
                "language": "python",
            })

            assert response.status_code == 200
            data = response.json()
            assert data["structured"] is not None
            assert data["structured"]["purpose"] == "Greets user."
            assert "**Purpose**" in data["explanation"]
        finally:
            set_resolver(None)
