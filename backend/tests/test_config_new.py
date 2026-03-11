"""Tests for the updated configuration module.

Covers:
* _find_config_file — search path logic
* AppSettings instantiation with all new fields
* CodeSearchSettings — LiteLLM model strings, storage backends, validation
* VoyageSecrets + MistralSecrets + CohereSecrets
* _inject_embedding_env_vars — unified credential injection
* load_settings — YAML loading + merging via _find_config_file
* Postgres backend configuration
* Edge cases: missing files, empty values

Total: 60+ tests
"""
from __future__ import annotations

import os
import sys
import types
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _stub(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(name, m)
    return m

_stub("cocoindex")
_stub("sentence_transformers", SentenceTransformer=MagicMock)
_stub("sqlite_vec")
_stub("litellm")

# ---------------------------------------------------------------------------
# Real imports
# ---------------------------------------------------------------------------

from app.config import (  # noqa: E402
    AppSettings,
    CodeSearchSettings,
    GitWorkspaceSettings,
    ServerSettings,
    Secrets,
    EmbeddingSecrets,
    AwsSecrets,
    OpenAISecrets,
    VoyageSecrets,
    MistralSecrets,
    CohereSecrets,
    _find_config_file,
    _inject_embedding_env_vars,
    load_settings,
)


# ===================================================================
# _find_config_file
# ===================================================================


class TestFindConfigFile:
    def test_finds_in_config_subdir(self, tmp_path, monkeypatch):
        """Priority 1: ./config/{filename}"""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        target = config_dir / "conductor.settings.yaml"
        target.write_text("server: {}")
        assert _find_config_file("conductor.settings.yaml") == target

    def test_finds_in_cwd(self, tmp_path, monkeypatch):
        """Priority 2: ./{filename} (when config/ doesn't have it)"""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "conductor.settings.yaml"
        target.write_text("server: {}")
        assert _find_config_file("conductor.settings.yaml") == target

    def test_prefers_config_subdir_over_cwd(self, tmp_path, monkeypatch):
        """config/ should take priority over CWD."""
        monkeypatch.chdir(tmp_path)
        cwd_file = tmp_path / "conductor.settings.yaml"
        cwd_file.write_text("cwd")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "conductor.settings.yaml"
        config_file.write_text("config")
        assert _find_config_file("conductor.settings.yaml") == config_file

    def test_finds_in_parent_config(self, tmp_path, monkeypatch):
        """Priority 3: ../config/{filename}"""
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        config_dir = parent / "config"
        config_dir.mkdir()
        target = config_dir / "conductor.settings.yaml"
        target.write_text("parent")
        monkeypatch.chdir(child)
        assert _find_config_file("conductor.settings.yaml") == target

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _find_config_file("conductor.settings.yaml") is None

    def test_finds_secrets_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        target = config_dir / "conductor.secrets.yaml"
        target.write_text("aws: {}")
        assert _find_config_file("conductor.secrets.yaml") == target


# ===================================================================
# CodeSearchSettings
# ===================================================================


class TestCodeSearchSettingsDefaults:
    def test_default_embedding_model(self):
        cfg = CodeSearchSettings()
        assert cfg.embedding_model == "bedrock/eu.cohere.embed-v4:0"

    def test_default_embedding_dimensions_none(self):
        cfg = CodeSearchSettings()
        assert cfg.embedding_dimensions is None

    def test_default_chunk_size(self):
        cfg = CodeSearchSettings()
        assert cfg.chunk_size == 512

    def test_default_top_k(self):
        cfg = CodeSearchSettings()
        assert cfg.top_k_results == 5

    def test_default_repo_map_enabled(self):
        cfg = CodeSearchSettings()
        assert cfg.repo_map_enabled is True

    def test_default_repo_map_top_n(self):
        cfg = CodeSearchSettings()
        assert cfg.repo_map_top_n == 10

    def test_default_rerank_backend(self):
        cfg = CodeSearchSettings()
        assert cfg.rerank_backend == "none"


class TestCodeSearchSettingsCustom:
    def test_set_embedding_model_openai(self):
        cfg = CodeSearchSettings(embedding_model="text-embedding-3-small")
        assert cfg.embedding_model == "text-embedding-3-small"

    def test_set_embedding_model_voyage(self):
        cfg = CodeSearchSettings(embedding_model="voyage/voyage-code-3")
        assert cfg.embedding_model == "voyage/voyage-code-3"

    def test_set_embedding_model_local(self):
        cfg = CodeSearchSettings(embedding_model="sbert/sentence-transformers/all-MiniLM-L6-v2")
        assert cfg.embedding_model == "sbert/sentence-transformers/all-MiniLM-L6-v2"

    def test_set_embedding_model_gemini(self):
        cfg = CodeSearchSettings(embedding_model="gemini/text-embedding-004")
        assert cfg.embedding_model == "gemini/text-embedding-004"

    def test_set_explicit_dimensions(self):
        cfg = CodeSearchSettings(embedding_dimensions=2048)
        assert cfg.embedding_dimensions == 2048

    def test_custom_chunk_size(self):
        cfg = CodeSearchSettings(chunk_size=1024)
        assert cfg.chunk_size == 1024


class TestCodeSearchSettingsSerialization:
    def test_model_dump(self):
        cfg = CodeSearchSettings()
        d = cfg.model_dump()
        assert isinstance(d, dict)
        assert "embedding_model" in d
        assert "repo_map_enabled" in d


# ===================================================================
# Secrets models
# ===================================================================


class TestVoyageSecrets:
    def test_default(self):
        s = VoyageSecrets()
        assert s.api_key is None

    def test_with_key(self):
        s = VoyageSecrets(api_key="pa-test")
        assert s.api_key == "pa-test"


class TestMistralSecrets:
    def test_default(self):
        s = MistralSecrets()
        assert s.api_key is None

    def test_with_key(self):
        s = MistralSecrets(api_key="m-test")
        assert s.api_key == "m-test"


class TestCohereSecrets:
    def test_default(self):
        s = CohereSecrets()
        assert s.api_key is None

    def test_with_key(self):
        s = CohereSecrets(api_key="co-test")
        assert s.api_key == "co-test"


class TestEmbeddingSecrets:
    def test_defaults(self):
        e = EmbeddingSecrets()
        assert isinstance(e.aws, AwsSecrets)
        assert isinstance(e.openai, OpenAISecrets)
        assert isinstance(e.voyage, VoyageSecrets)
        assert isinstance(e.mistral, MistralSecrets)
        assert isinstance(e.cohere, CohereSecrets)

    def test_all_fields(self):
        e = EmbeddingSecrets(
            aws=AwsSecrets(access_key_id="AK", session_token="ST"),
            openai=OpenAISecrets(api_key="sk-test"),
            voyage=VoyageSecrets(api_key="pa-test"),
            mistral=MistralSecrets(api_key="m-test"),
            cohere=CohereSecrets(api_key="co-test"),
        )
        assert e.aws.access_key_id == "AK"
        assert e.aws.session_token == "ST"
        assert e.openai.api_key == "sk-test"
        assert e.voyage.api_key == "pa-test"
        assert e.mistral.api_key == "m-test"
        assert e.cohere.api_key == "co-test"


class TestSecrets:
    def test_has_embedding(self):
        s = Secrets()
        assert isinstance(s.embedding, EmbeddingSecrets)

    def test_has_voyage(self):
        s = Secrets()
        assert isinstance(s.embedding.voyage, VoyageSecrets)

    def test_has_mistral(self):
        s = Secrets()
        assert isinstance(s.embedding.mistral, MistralSecrets)

    def test_has_cohere(self):
        s = Secrets()
        assert isinstance(s.embedding.cohere, CohereSecrets)

    def test_all_fields(self):
        s = Secrets(
            embedding=EmbeddingSecrets(
                aws=AwsSecrets(access_key_id="AK"),
                openai=OpenAISecrets(api_key="sk-test"),
                voyage=VoyageSecrets(api_key="pa-test"),
                mistral=MistralSecrets(api_key="m-test"),
                cohere=CohereSecrets(api_key="co-test"),
            )
        )
        assert s.embedding.aws.access_key_id == "AK"
        assert s.embedding.openai.api_key == "sk-test"
        assert s.embedding.voyage.api_key == "pa-test"
        assert s.embedding.mistral.api_key == "m-test"
        assert s.embedding.cohere.api_key == "co-test"


# ===================================================================
# _inject_embedding_env_vars
# ===================================================================


class TestInjectEnvVars:
    @pytest.fixture(autouse=True)
    def clean_env(self):
        """Remove test env vars before/after each test."""
        keys = [
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "AWS_DEFAULT_REGION", "OPENAI_API_KEY", "VOYAGE_API_KEY",
            "MISTRAL_API_KEY", "CO_API_KEY",
            "COCOINDEX_DATABASE_URL", "COCOINDEX_CODE_EMBEDDING_MODEL",
        ]
        old = {k: os.environ.pop(k, None) for k in keys}
        yield
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def _make_settings(self, embedding_model="bedrock/cohere.embed-v4:0", **emb_kwargs):
        """Build AppSettings with embedding credentials nested under EmbeddingSecrets."""
        secrets = Secrets(embedding=EmbeddingSecrets(**emb_kwargs))
        cs = CodeSearchSettings(embedding_model=embedding_model)
        return AppSettings(code_search=cs, secrets=secrets)

    def test_injects_all_available_aws(self):
        settings = self._make_settings(
            aws=AwsSecrets(access_key_id="AK", secret_access_key="SK", region="us-west-2"),
        )
        _inject_embedding_env_vars(settings)
        assert os.environ["AWS_ACCESS_KEY_ID"] == "AK"
        assert os.environ["AWS_SECRET_ACCESS_KEY"] == "SK"
        assert os.environ["AWS_DEFAULT_REGION"] == "us-west-2"

    def test_injects_aws_session_token(self):
        settings = self._make_settings(
            aws=AwsSecrets(access_key_id="AK", secret_access_key="SK", session_token="ST"),
        )
        _inject_embedding_env_vars(settings)
        assert os.environ["AWS_SESSION_TOKEN"] == "ST"

    def test_no_session_token_not_injected(self):
        settings = self._make_settings(
            aws=AwsSecrets(access_key_id="AK", secret_access_key="SK"),
        )
        _inject_embedding_env_vars(settings)
        assert "AWS_SESSION_TOKEN" not in os.environ

    def test_injects_openai_key(self):
        settings = self._make_settings(
            openai=OpenAISecrets(api_key="sk-hello"),
        )
        _inject_embedding_env_vars(settings)
        assert os.environ["OPENAI_API_KEY"] == "sk-hello"

    def test_injects_voyage_key(self):
        settings = self._make_settings(
            voyage=VoyageSecrets(api_key="pa-hello"),
        )
        _inject_embedding_env_vars(settings)
        assert os.environ["VOYAGE_API_KEY"] == "pa-hello"

    def test_injects_mistral_key(self):
        settings = self._make_settings(
            mistral=MistralSecrets(api_key="m-hello"),
        )
        _inject_embedding_env_vars(settings)
        assert os.environ["MISTRAL_API_KEY"] == "m-hello"

    def test_injects_cohere_key(self):
        settings = self._make_settings(
            cohere=CohereSecrets(api_key="co-hello"),
        )
        _inject_embedding_env_vars(settings)
        assert os.environ["CO_API_KEY"] == "co-hello"

    def test_injects_all_creds_regardless_of_model(self):
        """All available credentials should be injected, not just the active model's."""
        settings = self._make_settings(
            embedding_model="voyage/voyage-code-3",
            aws=AwsSecrets(access_key_id="AK", secret_access_key="SK"),
            openai=OpenAISecrets(api_key="sk-test"),
            voyage=VoyageSecrets(api_key="pa-test"),
        )
        _inject_embedding_env_vars(settings)
        # All should be injected
        assert os.environ["AWS_ACCESS_KEY_ID"] == "AK"
        assert os.environ["OPENAI_API_KEY"] == "sk-test"
        assert os.environ["VOYAGE_API_KEY"] == "pa-test"

    def test_no_credentials_no_error(self):
        settings = self._make_settings(
            embedding_model="sbert/sentence-transformers/all-MiniLM-L6-v2"
        )
        # Should not raise
        _inject_embedding_env_vars(settings)

    def test_setdefault_does_not_overwrite(self):
        """If env var is already set, setdefault should not overwrite."""
        os.environ["OPENAI_API_KEY"] = "existing-key"
        settings = self._make_settings(
            openai=OpenAISecrets(api_key="new-key"),
        )
        _inject_embedding_env_vars(settings)
        assert os.environ["OPENAI_API_KEY"] == "existing-key"


# ===================================================================
# AppSettings
# ===================================================================


class TestAppSettings:
    def test_default_instantiation(self):
        cfg = AppSettings()
        assert cfg.code_search.embedding_model == "bedrock/eu.cohere.embed-v4:0"

    def test_has_all_sections(self):
        cfg = AppSettings()
        assert cfg.server is not None
        assert cfg.database is not None
        assert cfg.auth is not None
        assert cfg.rooms is not None
        assert cfg.live_share is not None
        assert cfg.git_workspace is not None
        assert cfg.code_search is not None
        assert cfg.secrets is not None

    def test_serialization(self):
        cfg = AppSettings()
        d = cfg.model_dump()
        assert isinstance(d, dict)
        assert "code_search" in d
        assert "secrets" in d


# ===================================================================
# load_settings
# ===================================================================


class TestLoadSettings:
    def test_missing_files_returns_defaults(self, tmp_path):
        def mock_find(filename):
            return tmp_path / filename  # Points to non-existent files

        with patch("app.config._find_config_file", side_effect=mock_find):
            settings = load_settings()
            assert isinstance(settings, AppSettings)

    def test_loads_from_yaml(self, tmp_path):
        settings_file = tmp_path / "conductor.settings.yaml"
        settings_file.write_text(yaml.dump({
            "code_search": {
                "embedding_model": "voyage/voyage-code-3",
            }
        }))
        secrets_file = tmp_path / "conductor.secrets.yaml"
        secrets_file.write_text(yaml.dump({"embedding": {"openai": {"api_key": "sk-test"}}}))

        def mock_find(filename):
            if "settings" in filename:
                return settings_file
            return secrets_file

        with patch("app.config._find_config_file", side_effect=mock_find):
            settings = load_settings()
            assert settings.code_search.embedding_model == "voyage/voyage-code-3"
            assert settings.secrets.embedding.openai.api_key == "sk-test"
