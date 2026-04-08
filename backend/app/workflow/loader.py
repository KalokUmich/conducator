"""Workflow and agent config loader.

Loads workflow YAML files and agent .md files (YAML frontmatter + Markdown body)
from the config directory. Resolves agent references, delegate workflows,
core tool expansion, and validates input/output declarations.

Config file search order:
  1. ./config/{path}
  2. ../config/{path}
  3. ~/.conductor/{path}
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List

import yaml

from .models import (
    AgentConfig,
    AgentLimits,
    BrainConfig,
    QualityConfig,
    SwarmConfig,
    ToolsConfig,
    TriggerConfig,
)

logger = logging.getLogger(__name__)

# Regex to split YAML frontmatter from Markdown body
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Config file discovery
# ---------------------------------------------------------------------------

_CONFIG_SEARCH_DIRS: List[str] = []


def _find_config_dir() -> Path:
    """Find the config directory. Caches the result."""
    if _CONFIG_SEARCH_DIRS:
        return Path(_CONFIG_SEARCH_DIRS[0])

    candidates = [
        Path.cwd() / "config",
        Path.cwd().parent / "config",
        Path.home() / ".conductor",
    ]
    for p in candidates:
        if p.is_dir():
            _CONFIG_SEARCH_DIRS.append(str(p))
            return p

    # Fallback: use ./config even if it doesn't exist yet
    fallback = Path.cwd() / "config"
    _CONFIG_SEARCH_DIRS.append(str(fallback))
    return fallback


def _resolve_path(relative_path: str) -> Path:
    """Resolve a config-relative path to an absolute path."""
    config_dir = _find_config_dir()
    resolved = config_dir / relative_path
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {relative_path}\nSearched in: {config_dir}")
    return resolved


# ---------------------------------------------------------------------------
# Agent loader
# ---------------------------------------------------------------------------


def load_agent(path: str) -> AgentConfig:
    """Load an agent definition from a .md file with YAML frontmatter.

    Args:
        path: Config-relative path (e.g. "agents/security.md").

    Returns:
        Populated AgentConfig with instructions from the Markdown body.

    Raises:
        FileNotFoundError: If the agent file doesn't exist.
        ValueError: If frontmatter is missing or invalid.
    """
    resolved = _resolve_path(path)
    content = resolved.read_text(encoding="utf-8")

    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError(f"Agent file missing YAML frontmatter (--- markers): {path}")

    frontmatter_text = match.group(1)
    body = match.group(2).strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML frontmatter in {path}: {exc}") from exc

    if not isinstance(frontmatter, dict):
        raise ValueError(f"Frontmatter must be a YAML mapping in {path}")

    # Normalize tools config — supports both formats:
    #   New (flat list): tools: [grep, read_file, ...]
    #   Legacy (dict):   tools: {core: true, extra: [...]}
    tools_raw = frontmatter.pop("tools", {})
    if isinstance(tools_raw, list):
        tools = ToolsConfig(core=False, extra=tools_raw)  # new Brain format: flat list
    elif isinstance(tools_raw, dict):
        tools = ToolsConfig(**tools_raw)  # legacy format
    else:
        tools = ToolsConfig()

    # Normalize trigger config
    trigger_raw = frontmatter.pop("trigger", {})
    if isinstance(trigger_raw, dict):
        trigger = TriggerConfig(**trigger_raw)
    else:
        trigger = TriggerConfig()

    # Normalize limits config (new Brain format)
    limits_raw = frontmatter.pop("limits", {})
    if isinstance(limits_raw, dict):
        limits = AgentLimits(**limits_raw)
    else:
        limits = AgentLimits()

    # Normalize quality config
    quality_raw = frontmatter.pop("quality", {})
    if isinstance(quality_raw, dict):
        quality = QualityConfig(**quality_raw)
    else:
        quality = QualityConfig()

    return AgentConfig(
        tools=tools,
        trigger=trigger,
        limits=limits,
        quality=quality,
        instructions=body,
        source_path=str(resolved),
        **frontmatter,
    )


# ---------------------------------------------------------------------------
# Brain config loader
# ---------------------------------------------------------------------------


def load_brain_config() -> BrainConfig:
    """Load the Brain orchestrator configuration from brain.yaml.

    Returns:
        BrainConfig with limits, core_tools, and model settings.
        Falls back to defaults if brain.yaml doesn't exist.
    """
    try:
        resolved = _resolve_path("brain.yaml")
        data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning("brain.yaml is not a mapping, using defaults")
            return BrainConfig()
        return BrainConfig(**data)
    except FileNotFoundError:
        logger.info("brain.yaml not found, using default Brain config")
        return BrainConfig()
    except Exception as exc:
        logger.warning("Failed to load brain.yaml: %s — using defaults", exc)
        return BrainConfig()


def load_pr_brain_config():
    """Load the PR Brain configuration from brains/pr_review.yaml.

    Returns:
        PRBrainConfig with review agents, budget weights, and post-processing settings.
        Falls back to defaults if brains/pr_review.yaml doesn't exist.
    """
    from .models import PRBrainConfig

    try:
        resolved = _resolve_path("brains/pr_review.yaml")
        data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning("brains/pr_review.yaml is not a mapping, using defaults")
            return PRBrainConfig()
        return PRBrainConfig(**data)
    except FileNotFoundError:
        logger.info("brains/pr_review.yaml not found, using default PR Brain config")
        return PRBrainConfig()
    except Exception as exc:
        logger.warning("Failed to load brains/pr_review.yaml: %s — using defaults", exc)
        return PRBrainConfig()


# ---------------------------------------------------------------------------
# Swarm preset loader
# ---------------------------------------------------------------------------


def load_swarm(path: str) -> SwarmConfig:
    """Load a swarm preset from a YAML file.

    Args:
        path: Config-relative path (e.g. "swarms/pr_review.yaml").

    Returns:
        SwarmConfig with agent list and mode.
    """
    resolved = _resolve_path(path)
    data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Swarm file must be a YAML mapping: {path}")
    return SwarmConfig(**data)


def load_swarm_registry() -> Dict[str, SwarmConfig]:
    """Load all swarm presets from config/swarms/*.yaml.

    Returns:
        Dict mapping swarm name to SwarmConfig.
    """
    config_dir = _find_config_dir()
    swarms_dir = config_dir / "swarms"
    if not swarms_dir.is_dir():
        logger.info("No swarms directory found at %s", swarms_dir)
        return {}

    result: Dict[str, SwarmConfig] = {}
    for yaml_file in sorted(swarms_dir.glob("*.yaml")):
        rel_path = f"swarms/{yaml_file.name}"
        try:
            swarm = load_swarm(rel_path)
            result[swarm.name] = swarm
            logger.info("Loaded swarm '%s': %d agents, mode=%s", swarm.name, len(swarm.agents), swarm.mode)
        except Exception as exc:
            logger.error("Failed to load swarm %s: %s", rel_path, exc)

    return result


# ---------------------------------------------------------------------------
# Agent registry loader (for Brain mode)
# ---------------------------------------------------------------------------


def load_agent_registry() -> Dict[str, AgentConfig]:
    """Load all agent definitions from config/agents/*.md.

    Returns:
        Dict mapping agent name to AgentConfig.
    """
    config_dir = _find_config_dir()
    agents_dir = config_dir / "agents"
    if not agents_dir.is_dir():
        logger.warning("No agents directory found at %s", agents_dir)
        return {}

    result: Dict[str, AgentConfig] = {}
    for md_file in sorted(agents_dir.glob("*.md")):
        rel_path = f"agents/{md_file.name}"
        try:
            agent = load_agent(rel_path)
            result[agent.name] = agent
        except Exception as exc:
            logger.error("Failed to load agent %s: %s", rel_path, exc)

    logger.info("Loaded agent registry: %d agents", len(result))
    return result
