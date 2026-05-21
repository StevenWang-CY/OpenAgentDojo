"""Config loads cleanly and honors env vars."""

from __future__ import annotations

import os

from app.config import Settings, get_settings


def test_settings_load() -> None:
    s = get_settings()
    assert s.arena_env in {"development", "test", "staging", "production"}
    assert s.sandbox_driver in {"local", "docker"}
    assert isinstance(s.cors_origins, list)
    assert len(s.session_secret) >= 32


def test_llm_provider_defaults_disabled(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_PROVIDER", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings()
    assert s.llm_provider == "disabled"


def test_llm_provider_bedrock_when_creds_present(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "dummy-token")
    s = Settings()
    assert s.llm_provider == "bedrock"
    # Sanity: region default is us-east-2 unless overridden.
    assert os.environ.get("AWS_REGION", "us-east-2") in {"us-east-2", s.aws_region}
