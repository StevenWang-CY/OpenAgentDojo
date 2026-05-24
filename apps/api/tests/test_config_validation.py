"""Settings raise for insecure production/staging values."""

from __future__ import annotations

import pytest


def _prod_env(monkeypatch, **overrides) -> None:
    """Apply a minimal valid-production env, then layer caller overrides on top."""
    base = {
        "ARENA_ENV": "production",
        "SESSION_SECRET": "a" * 64,
        # Required in staging/production AND must differ from SESSION_SECRET
        # (P2-B12). A different alphabet keeps the two demonstrably distinct.
        "SHARE_TOKEN_SECRET": "b" * 64,
        "RESEND_API_KEY": "re_xxx",
        "S3_ACCESS_KEY_ID": "akid",
        "S3_SECRET_ACCESS_KEY": "secret",
        "S3_BUCKET": "prod-bucket",
        "DATABASE_URL": "postgresql+asyncpg://u:p@prod-db:5432/arena",
        "WEB_ORIGIN": "https://arena.example",
        "ALLOWED_HOSTS": "arena.example",
        "ALLOW_DEV_AUTH": "false",
        # New prod invariants — see ``_validate_for_environment``:
        # the local sandbox driver has no isolation, and STARTTLS without
        # cert verification is open to passive MITM on real SMTP relays.
        "SANDBOX_DRIVER": "docker",
        "SMTP_VERIFY_CERTS": "true",
        # P0-5 — consent records hash the remote IP with this salt; an
        # empty or dev-prefixed value defeats the hash. 32+ chars is the
        # validator's floor.
        "IP_HASH_SALT": "c" * 48,
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
    # ``monkeypatch.delenv`` only removes the process env var, but
    # pydantic-settings continues to read the repo's ``.env`` file
    # (which carries a dev RESEND_API_KEY). Setting the env var to an
    # empty string is the canonical way to force-disable a field in this
    # codebase — see the matching pattern in the other secret-missing
    # tests above.
    monkeypatch.setenv("RESEND_API_KEY", "")
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


def test_missing_share_token_secret_rejected_in_production(monkeypatch) -> None:
    """``SHARE_TOKEN_SECRET`` must be explicit in staging/production (P2-B12).

    Note: the apps/api/.env file ships a dev value for this knob, so
    ``monkeypatch.delenv`` alone wouldn't clear it (pydantic-settings falls
    back to the .env file). We explicitly set it to an empty string instead
    — which is the production failure mode this guard exists to catch.
    """
    from app.config import Settings

    _prod_env(monkeypatch, SHARE_TOKEN_SECRET="")
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "SHARE_TOKEN_SECRET" in str(exc_info.value)


def test_share_token_secret_equal_to_session_secret_rejected(monkeypatch) -> None:
    """Sharing one secret across two trust domains conflates rotation."""
    from app.config import Settings

    _prod_env(monkeypatch, SHARE_TOKEN_SECRET="a" * 64)  # equal to SESSION_SECRET
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "SHARE_TOKEN_SECRET" in str(exc_info.value)


def test_short_share_token_secret_rejected_in_production(monkeypatch) -> None:
    """A 16-char share secret is too short to be cryptographically meaningful."""
    from app.config import Settings

    _prod_env(monkeypatch, SHARE_TOKEN_SECRET="abc123def456")  # < 32 chars
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert "SHARE_TOKEN_SECRET" in str(exc_info.value)


def test_share_token_secret_unrequired_in_development(monkeypatch) -> None:
    """Local dev should still load without ``SHARE_TOKEN_SECRET`` set.

    The strict guard only fires in staging/production — local laptops can
    leave the share secret unset (or use the apps/api/.env dev fallback)
    without breaking the boot.
    """
    from app.config import Settings

    monkeypatch.setenv("ARENA_ENV", "development")
    monkeypatch.setenv("SESSION_SECRET", "dev-secret-change-me-32-chars-min-aaaa")
    monkeypatch.setenv("SHARE_TOKEN_SECRET", "")
    s = Settings()
    assert s.arena_env == "development"
