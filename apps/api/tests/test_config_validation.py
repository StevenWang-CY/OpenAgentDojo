"""Settings raise for insecure production/staging values."""

from __future__ import annotations

import pytest


def _prod_env(monkeypatch, **overrides) -> None:
    """Apply a minimal valid-production env, then layer caller overrides on top."""
    base = {
        "ARENA_ENV": "production",
        "SESSION_SECRET": "a" * 64,
        "RESEND_API_KEY": "re_xxx",
        "S3_ACCESS_KEY_ID": "akid",
        "S3_SECRET_ACCESS_KEY": "secret",
        "S3_BUCKET": "prod-bucket",
        "DATABASE_URL": "postgresql+asyncpg://u:p@prod-db:5432/arena",
        "WEB_ORIGIN": "https://arena.example",
        "ALLOWED_HOSTS": "arena.example",
        "ALLOW_DEV_AUTH": "false",
    }
    for k, v in {**base, **overrides}.items():
        monkeypatch.setenv(k, v)


def test_session_secret_must_not_start_with_dev_prefix(monkeypatch) -> None:
    from app.config import Settings

    _prod_env(monkeypatch, SESSION_SECRET="dev-" + ("a" * 60))
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "SESSION_SECRET" in str(exc_info.value)


def test_short_session_secret_rejected_in_production(monkeypatch) -> None:
    from app.config import Settings

    _prod_env(monkeypatch, SESSION_SECRET="a" * 40)  # >32 (field min) but <64
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "64" in str(exc_info.value)


def test_missing_resend_key_rejected_in_production(monkeypatch) -> None:
    from app.config import Settings

    _prod_env(monkeypatch)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "RESEND_API_KEY" in str(exc_info.value)


def test_http_web_origin_rejected_in_production(monkeypatch) -> None:
    from app.config import Settings

    _prod_env(monkeypatch, WEB_ORIGIN="http://arena.example")
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "WEB_ORIGIN" in str(exc_info.value)


def test_wildcard_allowed_hosts_rejected_in_production(monkeypatch) -> None:
    from app.config import Settings

    _prod_env(monkeypatch, ALLOWED_HOSTS="*")
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "ALLOWED_HOSTS" in str(exc_info.value)


def test_valid_production_config_loads(monkeypatch) -> None:
    from app.config import Settings

    _prod_env(monkeypatch)
    s = Settings()
    assert s.arena_env == "production"
    assert s.cookie_secure is True
    assert s.allowed_hosts_list == ["arena.example"]


def test_development_env_remains_permissive(monkeypatch) -> None:
    """Local-dev defaults must still load — no surprise breakage."""
    from app.config import Settings

    monkeypatch.setenv("ARENA_ENV", "development")
    monkeypatch.setenv("SESSION_SECRET", "dev-secret-change-me-32-chars-min-aaaa")
    s = Settings()
    assert s.arena_env == "development"
    assert s.cookie_secure is False


def test_allow_dev_auth_rejected_in_production(monkeypatch) -> None:
    """Dev IP-keyed auth fallback must not be enableable outside development."""
    from app.config import Settings

    _prod_env(monkeypatch, ALLOW_DEV_AUTH="true")
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "ALLOW_DEV_AUTH" in str(exc_info.value)
