"""Claude Bedrock provider implementation.

This module provides an AIProvider implementation that connects to Claude
via AWS Bedrock service.

Usage:
    provider = ClaudeBedrockProvider(
        aws_access_key_id="...",
        aws_secret_access_key="...",
        region_name="us-east-1"
    )
    if provider.health_check():
        summary = provider.summarize_structured(messages)
"""
import json
import logging
from typing import List, Optional

from .base import AIProvider, ChatMessage, DecisionSummary
from .prompts import get_summary_prompt

logger = logging.getLogger(__name__)


class ClaudeBedrockProvider(AIProvider):
    """AIProvider implementation using Claude via AWS Bedrock.

    This provider connects to Claude through AWS Bedrock, which requires
    AWS credentials with appropriate Bedrock permissions.

    Attributes:
        aws_access_key_id: AWS access key ID.
        aws_secret_access_key: AWS secret access key.
        region_name: AWS region for Bedrock service.
        model_id: Bedrock model ID for Claude.
    """

    DEFAULT_MODEL_ID = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    DEFAULT_REGION = "us-east-1"

    def __init__(
        self,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        region_name: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        """Initialize the Claude Bedrock provider.

        Args:
            aws_access_key_id: AWS access key ID. If None, uses default credential chain.
            aws_secret_access_key: AWS secret access key.
            aws_session_token: Optional AWS session token for temporary credentials.
            region_name: AWS region for Bedrock. Defaults to us-east-1.
            model_id: Bedrock model ID. Defaults to Claude 3 Sonnet.
        """
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.aws_session_token = aws_session_token
        self.region_name = region_name or self.DEFAULT_REGION
        self.model_id = model_id or self.DEFAULT_MODEL_ID
        self._client: Optional[object] = None

    def _get_client(self) -> object:
        """Get or create the Bedrock runtime client.

        Returns:
            Boto3 Bedrock runtime client.

        Raises:
            ImportError: If boto3 package is not installed.
        """
        if self._client is None:
            try:
                import boto3
                kwargs = {"region_name": self.region_name}
                if self.aws_access_key_id and self.aws_secret_access_key:
                    kwargs["aws_access_key_id"] = self.aws_access_key_id
                    kwargs["aws_secret_access_key"] = self.aws_secret_access_key
                if self.aws_session_token:
                    kwargs["aws_session_token"] = self.aws_session_token
                self._client = boto3.client("bedrock-runtime", **kwargs)
            except ImportError:
                raise ImportError(
                    "boto3 package is required for ClaudeBedrockProvider. "
                    "Install it with: pip install boto3"
                )
        return self._client

    def health_check(self) -> bool:
        """Check if Claude via Bedrock is accessible.

        Attempts a minimal API call to verify connectivity.

        Returns:
            bool: True if Bedrock is accessible, False otherwise.
        """
        try:
            client = self._get_client()
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            })
            client.invoke_model(modelId=self.model_id, body=body)
            return True
        except Exception as e:
            logger.warning(f"Claude Bedrock health check failed: {e}")
            return False

    def summarize(self, messages: List[str]) -> str:
        """Generate a summary of the provided messages using Claude via Bedrock.

        Args:
            messages: List of message strings to summarize.

        Returns:
            str: A concise summary of the messages.

        Raises:
            Exception: If the API call fails.
        """
        if not messages:
            return ""

        client = self._get_client()
        combined_messages = "\n".join(messages)

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Please provide a concise summary of the following messages:\n\n"
                        f"{combined_messages}"
                    ),
                }
            ],
        })

        response = client.invoke_model(modelId=self.model_id, body=body)
        response_body = json.loads(response["body"].read())

        return response_body["content"][0]["text"]

    def summarize_structured(self, messages: List[ChatMessage]) -> DecisionSummary:
        """Generate a structured decision summary from chat messages.

        Args:
            messages: List of ChatMessage objects to summarize.

        Returns:
            DecisionSummary: A structured summary with topic, problem,
                solution, and other decision-related fields.

        Raises:
            Exception: If the API call fails or JSON parsing fails.
        """
        if not messages:
            return DecisionSummary()

        client = self._get_client()

        # Generate prompt using shared template
        prompt = get_summary_prompt(messages)

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        })

        response = client.invoke_model(modelId=self.model_id, body=body)
        response_body = json.loads(response["body"].read())
        response_text = response_body["content"][0]["text"].strip()

        # Parse JSON response
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {response_text}")
            raise ValueError(f"Invalid JSON response from AI: {e}")

        # Validate and extract fields with defaults
        return DecisionSummary(
            type="decision_summary",
            topic=data.get("topic", ""),
            problem_statement=data.get("problem_statement", ""),
            proposed_solution=data.get("proposed_solution", ""),
            requires_code_change=data.get("requires_code_change", False),
            affected_components=data.get("affected_components", []),
            risk_level=data.get("risk_level", "low"),
            next_steps=data.get("next_steps", []),
        )
