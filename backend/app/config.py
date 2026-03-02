"""Conducator application configuration.

Loads settings from two YAML files:
  * conductor.settings.yaml  — non-secret configuration
  * conductor.secrets.yaml   — secrets (never committed)

New in this version:
  * GitWorkspaceSettings — git worktree management
  * CodeSearchSettings   — CocoIndex code search
  * _inject_embedding_env_vars() helper
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SETTINGS_FILE = Path("conductor.settings.yaml")
SECRETS_FILE  = Path("conductor.secrets.yaml")


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        logger.warning("Config file not found: %s", path)
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# Secrets models
# ---------------------------------------------------------------------------


class AwsSecrets(BaseModel):
    access_key_id:     Optional[str] = None
    secret_access_key: Optional[str] = None
    region:            Optional[str] = "us-east-1"


class OpenAISecrets(BaseModel):
    api_key: Optional[str] = None


class DatabaseSecrets(BaseModel):
    url: Optional[str] = None


class JWTSecrets(BaseModel):
    secret_key: str = "change-me-in-production"
    algorithm:  str = "HS256"


class Secrets(BaseModel):
    aws:      AwsSecrets      = Field(default_factory=AwsSecrets)
    openai:   OpenAISecrets   = Field(default_factory=OpenAISecrets)
    database: DatabaseSecrets = Field(default_factory=DatabaseSecrets)
    jwt:      JWTSecrets      = Field(default_factory=JWTSecrets)


# ---------------------------------------------------------------------------
# Settings models
# ---------------------------------------------------------------------------


class ServerSettings(BaseModel):
    host:         str  = "0.0.0.0"
    port:         int  = 8000
    debug:        bool = False
    reload:       bool = False
    log_level:    str  = "info"
    allowed_origins: List[str] = Field(default_factory=lambda: ["*"])


class DatabaseSettings(BaseModel):
    pool_size:     int = 10
    max_overflow:  int = 20
    pool_timeout:  int = 30
    echo_sql:      bool = False


class AuthSettings(BaseModel):
    token_expire_minutes:   int  = 60
    refresh_expire_days:    int  = 7
    require_email_verify:   bool = False


class RoomSettings(BaseModel):
    max_participants:       int = 50
    max_rooms_per_user:     int = 10
    session_timeout_minutes: int = 120
    enable_persistence:     bool = True


class LiveShareSettings(BaseModel):
    """Kept for backwards compatibility; disabled by default in new deployments."""
    enabled:              bool = False
    vscode_extension_id:  str  = "ms-vsliveshare.vsliveshare"
    host_timeout_seconds: int  = 300


class GitWorkspaceSettings(BaseModel):
    """Configuration for the Git Workspace module."""
    enabled:                bool                    = True
    workspaces_dir:         str                     = "./workspaces"
    git_auth_mode:          Literal["token", "delegate"] = "token"
    credential_ttl_seconds: int                     = 3600
    max_worktrees_per_repo: int                     = 20
    cleanup_on_room_close:  bool                    = True


class CodeSearchSettings(BaseModel):
    """Configuration for CocoIndex Code Search."""
    enabled:             bool                           = True
    index_dir:           str                            = "./cocoindex_data"
    embedding_backend:   Literal["local", "bedrock", "openai"] = "local"
    local_model_name:    str                            = "all-MiniLM-L6-v2"
    bedrock_model_id:    str                            = "amazon.titan-embed-text-v2:0"
    openai_model_name:   str                            = "text-embedding-3-small"
    chunk_size:          int                            = 512
    top_k_results:       int                            = 5


class AppSettings(BaseModel):
    server:         ServerSettings       = Field(default_factory=ServerSettings)
    database:       DatabaseSettings     = Field(default_factory=DatabaseSettings)
    auth:           AuthSettings         = Field(default_factory=AuthSettings)
    rooms:          RoomSettings         = Field(default_factory=RoomSettings)
    live_share:     LiveShareSettings    = Field(default_factory=LiveShareSettings)
    git_workspace:  GitWorkspaceSettings = Field(default_factory=GitWorkspaceSettings)
    code_search:    CodeSearchSettings   = Field(default_factory=CodeSearchSettings)
    secrets:        Secrets              = Field(default_factory=Secrets)


# ---------------------------------------------------------------------------
# Environment variable injection for CocoIndex
# ---------------------------------------------------------------------------


def _inject_embedding_env_vars(settings: AppSettings) -> None:
    """
    Inject credentials from conductor.secrets.yaml into environment variables
    so that CocoIndex can pick them up without knowing about our secrets file.
    """
    backend = settings.code_search.embedding_backend
    secrets = settings.secrets

    if backend == "bedrock":
        if secrets.aws.access_key_id:
            os.environ["AWS_ACCESS_KEY_ID"]     = secrets.aws.access_key_id
        if secrets.aws.secret_access_key:
            os.environ["AWS_SECRET_ACCESS_KEY"] = secrets.aws.secret_access_key
        if secrets.aws.region:
            os.environ["AWS_DEFAULT_REGION"]    = secrets.aws.region
        logger.info("Injected AWS credentials into env for Bedrock embedding backend.")

    elif backend == "openai":
        if secrets.openai.api_key:
            os.environ["OPENAI_API_KEY"] = secrets.openai.api_key
        logger.info("Injected OpenAI API key into env for OpenAI embedding backend.")

    else:  # local
        logger.debug("Local embedding backend — no env var injection needed.")


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_settings() -> AppSettings:
    """Load and merge settings + secrets into a single *AppSettings* object."""
    settings_data = _load_yaml(SETTINGS_FILE)
    secrets_data  = _load_yaml(SECRETS_FILE)

    # Merge: secrets live under the "secrets" key in AppSettings
    settings_data["secrets"] = secrets_data

    app_settings = AppSettings(**settings_data)
    logger.info(
        "Settings loaded (server=%s:%s, git_workspace.enabled=%s, code_search.enabled=%s)",
        app_settings.server.host,
        app_settings.server.port,
        app_settings.git_workspace.enabled,
        app_settings.code_search.enabled,
    )
    return app_settings
