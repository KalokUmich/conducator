"""Prompt templates for AI providers.

This module contains shared prompt templates used by AI providers
for various tasks like summarization.
"""

# Prompt template for structured decision summarization
STRUCTURED_SUMMARY_PROMPT = """You are an AI assistant for software engineering decision summarization.

Your task is to analyze the following conversation between a host and an engineer, then extract key information about problems discussed, decisions made, and whether code changes are required.

## Conversation
{conversation}

## Instructions
1. Identify the main topic or subject being discussed
2. Extract the core problem or challenge being addressed
3. Summarize the proposed solution or approach (if any)
4. Determine if code changes are required based on the discussion
5. List any components, files, or systems that would be affected
6. Assess the risk level based on scope and complexity
7. Extract clear action items or next steps

## Output Requirements
Your output must be ONLY valid JSON with no markdown formatting, no code blocks, and no additional explanation.

If the conversation lacks sufficient information for a field:
- Use an empty string "" for text fields where no information is available
- Use false for requires_code_change if unclear
- Use an empty array [] for lists with no items
- Use "low" for risk_level if assessment is not possible
- For topic, provide "No clear topic identified" if truly unclear

## Required JSON Schema
{{
  "type": "decision_summary",
  "topic": "Brief topic of the discussion (1-2 sentences max)",
  "problem_statement": "Clear description of the problem or challenge being discussed",
  "proposed_solution": "The proposed solution, approach, or decision made",
  "requires_code_change": true or false,
  "affected_components": ["list", "of", "affected", "components", "files", "or", "systems"],
  "risk_level": "low" or "medium" or "high",
  "next_steps": ["actionable", "items", "or", "follow-up", "tasks"]
}}

## Risk Level Guidelines
- "low": Minor changes, well-understood scope, minimal dependencies
- "medium": Moderate changes, some complexity, affects multiple components
- "high": Major changes, high complexity, critical systems, or unclear scope

Output only the JSON object:"""


def format_conversation(messages: list) -> str:
    """Format a list of chat messages into a conversation string.

    Args:
        messages: List of message objects with role, text, and timestamp attributes.

    Returns:
        Formatted conversation string with role labels and timestamps.
    """
    if not messages:
        return "(No messages in conversation)"

    lines = []
    for msg in messages:
        role_label = "[Host]" if msg.role == "host" else "[Engineer]"
        lines.append(f"{role_label}: {msg.text}")

    return "\n".join(lines)


def get_summary_prompt(messages: list) -> str:
    """Generate a complete summary prompt for the given messages.

    Args:
        messages: List of chat message objects.

    Returns:
        Complete prompt string ready for AI model input.
    """
    conversation = format_conversation(messages)
    return STRUCTURED_SUMMARY_PROMPT.format(conversation=conversation)


# Template for generating code prompts from decision summaries
CODE_PROMPT_TEMPLATE = """You are a senior software engineer tasked with implementing code changes.

## Problem Statement
{problem_statement}

## Proposed Solution
{proposed_solution}

## Target Components
{affected_components}

## Risk Level
{risk_level}

{context_section}
## Task
Based on the above information, implement the necessary code changes. Your output should be a unified diff format that can be applied to the codebase.

### Requirements:
1. Follow existing code patterns and conventions in the target components
2. Include appropriate error handling
3. Add or update tests if applicable
4. Ensure backward compatibility where possible
5. Document any breaking changes

### Output Format:
Provide your changes as unified diff patches that can be applied with `git apply` or similar tools. Each file change should be clearly marked with the file path.

Begin implementation:"""


def get_code_prompt(
    problem_statement: str,
    proposed_solution: str,
    affected_components: list,
    risk_level: str,
    context_snippet: str = None,
) -> str:
    """Generate a code prompt from a decision summary.

    Constructs a prompt that instructs a code generation model to produce
    unified diff output suitable for code proposal generation.

    Args:
        problem_statement: Description of the problem to solve.
        proposed_solution: The proposed solution or approach.
        affected_components: List of components/files that may be affected.
        risk_level: Risk assessment (low, medium, high).
        context_snippet: Optional code snippet for additional context.

    Returns:
        Complete code prompt string ready for code generation model input.
    """
    # Format affected components as a bulleted list
    if affected_components:
        components_str = "\n".join(f"- {comp}" for comp in affected_components)
    else:
        components_str = "- (No specific components identified)"

    # Format context section if provided
    if context_snippet:
        context_section = f"""## Context
The following code snippet provides relevant context:

```
{context_snippet}
```

"""
    else:
        context_section = ""

    return CODE_PROMPT_TEMPLATE.format(
        problem_statement=problem_statement or "No problem statement provided.",
        proposed_solution=proposed_solution or "No solution proposed.",
        affected_components=components_str,
        risk_level=risk_level or "unknown",
        context_section=context_section,
    )

