"""Application settings — single source of truth for env-driven config.

All values are loaded once at import time via ``get_settings()`` (cached).
Mirrors the contract in the repo-root ``.env.example`` and §16.A of the
implementation plan.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _detect_repo_root() -> Path:
    """Return the repo root if this file lives under one, else a safe fallback.

    The on-disk layout is apps/api/app/config.py → repo-root via ``parents[3]``,
    but inside the API docker image the file lives at /app/app/config.py with
    only 2 parents above it. Falling back to ``/`` keeps the module importable
    in containers; ``MISSIONS_ROOT`` is set explicitly there.
    """
    here = Path(__file__).resolve()
    if len(here.parents) >= 4:
        return here.parents[3]
    return here.parents[-1]


_REPO_ROOT = _detect_repo_root()
_DEFAULT_MISSIONS_ROOT = _REPO_ROOT / "missions"
_API_DIR = _REPO_ROOT / "apps" / "api"


class Settings(BaseSettings):
    """Strongly-typed application settings.

    Loaded from environment variables, with ``apps/api/.env`` consulted as a
    fallback for local development.
    """

    model_config = SettingsConfigDict(
        env_file=(str(_API_DIR / ".env"), str(_REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime env ---
    arena_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"

    # --- database ---
    database_url: str = "postgresql+asyncpg://arena:arena@localhost:5432/arena"
    sync_database_url: str = "postgresql://arena:arena@localhost:5432/arena"

    # --- redis / queue ---
    redis_url: str = "redis://localhost:6379/0"

    # --- s3 / object storage ---
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket: str = "arena-artifacts"
    s3_region: str = "us-east-1"

    # --- sandbox ---
    sandbox_driver: Literal["local", "docker"] = "local"
    sandbox_workdir: Path = Path("/tmp/arena-sandboxes")
    sandbox_max_concurrent: int = 10
    sandbox_timeout_seconds: int = 1800

    # --- auth ---
    session_cookie_name: str = "arena_session"
    session_secret: str = Field(
        default="dev-secret-change-me-32-chars-min-aaaa",
        min_length=32,
    )
    magic_link_ttl_minutes: int = 30
    resend_api_key: str | None = None
    email_from: str = "hello@arena.local"
    # SMTP fallback when Resend is not configured. Defaults match Mailhog so
    # local dev keeps working out of the box.
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_start_tls: bool = False
    # Share-token JWT signing secret. Falls back to ``session_secret`` for dev
    # so an existing .env keeps working; staging/production should set their
    # own dedicated value.
    share_token_secret: str | None = None

    # --- features ---
    feature_llm_narration: bool = False

    # --- workers ---
    # When True (default) provisioning runs in the API process so the resulting
    # SandboxHandle lands on the shared sandbox pool. Set False to enqueue via
    # RQ — requires moving the pool to the worker process (see plan §M8).
    provision_in_process: bool = True

    # --- auth dev fallbacks ---
    # When True AND ``arena_env == 'development'``, missing cookies fall back
    # to a deterministic dev user keyed by client IP (see auth/deps.py). Forced
    # OFF for staging/production by ``_validate_for_environment`` below.
    allow_dev_auth: bool = True

    # --- LLM provider (see plan §16.A) ---
    anthropic_provider: str | None = None  # "" / None / "bedrock"
    aws_bearer_token_bedrock: str | None = None
    aws_region: str = "us-east-2"
    anthropic_api_key: str | None = None

    # --- observability ---
    otel_exporter_otlp_endpoint: str | None = None

    # --- frontend / CORS ---
    # Origin allowed by CORS. In dev that's the Next.js dev server on :3000.
    # The legacy alias ``NEXT_PUBLIC_API_BASE_URL`` was historically reused for
    # this purpose; we still read it as a fallback so older .env files keep
    # working, but new deployments should set ``WEB_ORIGIN`` instead.
    web_origin: str = "http://localhost:3000"
    next_public_api_base_url: str | None = None

    # --- security: trusted hosts ---
    # Comma-separated list of Host header values that are accepted. Default
    # is permissive for local development; staging and production MUST set an
    # explicit non-wildcard list (enforced by ``_validate_for_environment``).
    allowed_hosts: str = "*"

    # --- mission content ---
    missions_root: Path = _DEFAULT_MISSIONS_ROOT

    # --- helpers ---
    @field_validator("missions_root", mode="before")
    @classmethod
    def _coerce_missions_root(cls, v: object) -> Path:
        if v is None or v == "":
            return _DEFAULT_MISSIONS_ROOT
        return Path(str(v)).expanduser().resolve()

    @field_validator("sandbox_workdir", mode="before")
    @classmethod
    def _coerce_sandbox_workdir(cls, v: object) -> Path:
        return Path(str(v)).expanduser()

    @property
    def cors_origins(self) -> list[str]:
        """Origins permitted to call the API."""
        # We deliberately keep this tight in MVP — just the configured frontend URL.
        origin = self.web_origin or "http://localhost:3000"
        return [origin]

    @property
    def llm_provider(self) -> Literal["bedrock", "direct", "disabled"]:
        provider = (self.anthropic_provider or "").strip().lower()
        if provider == "bedrock" and self.aws_bearer_token_bedrock:
            return "bedrock"
        if self.anthropic_api_key:
            return "direct"
        return "disabled"

    @property
    def cookie_secure(self) -> bool:
        """Whether session/CSRF cookies should set the ``Secure`` flag.

        True for every environment except local development so cookies cannot
        be sent over plaintext HTTP in test / staging / production.
        """
        return self.arena_env != "development"

    @property
    def allowed_hosts_list(self) -> list[str]:
        """Parse ``ALLOWED_HOSTS`` (comma-separated) into a list."""
        raw = (self.allowed_hosts or "").strip()
        if not raw:
            return ["*"]
        return [h.strip() for h in raw.split(",") if h.strip()]

    @model_validator(mode="after")
    def _validate_for_environment(self) -> Settings:
        """Hard production/staging guardrails — fail boot on insecure config."""
        if self.arena_env not in {"staging", "production"}:
            return self

        # The dev-user auth fallback must never be enabled outside development.
        if self.allow_dev_auth:
            raise ValueError(
                "ALLOW_DEV_AUTH must be False in staging/production "
                "(the dev IP-keyed user fallback is a security hole)"
            )

        # ---- session_secret strength ----
        secret = self.session_secret or ""
        if secret.startswith("dev-"):
            raise ValueError("SESSION_SECRET must not start with 'dev-' in staging/production")
        if len(secret) < 64:
            raise ValueError("SESSION_SECRET must be >=64 chars in staging/production")

        # ---- required externals ----
        missing: list[str] = []
        if not self.resend_api_key:
            missing.append("RESEND_API_KEY")
        if not self.s3_access_key_id:
            missing.append("S3_ACCESS_KEY_ID")
        if not self.s3_secret_access_key:
            missing.append("S3_SECRET_ACCESS_KEY")
        if not self.s3_bucket or self.s3_bucket == "arena-artifacts":
            missing.append("S3_BUCKET")
        if (
            not self.database_url
            or self.database_url == "postgresql+asyncpg://arena:arena@localhost:5432/arena"
        ):
            missing.append("DATABASE_URL")
        if missing:
            raise ValueError(f"missing required production settings: {', '.join(missing)}")

        # ---- WEB_ORIGIN must be real https ----
        origin = (self.web_origin or "").strip().lower()
        if not origin.startswith("https://"):
            raise ValueError("WEB_ORIGIN must be an https:// URL in staging/production")

        # ---- ALLOWED_HOSTS must be explicit ----
        hosts = self.allowed_hosts_list
        if not hosts or hosts == ["*"]:
            raise ValueError(
                "ALLOWED_HOSTS must be set explicitly (no wildcard) in staging/production"
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton settings instance."""
    return Settings()
