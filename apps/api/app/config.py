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

    # --- P0-5 cookie / privacy consent (legal pages) ---
    # Bump CONSENT_POLICY_VERSION any time the cookie/privacy policy text
    # changes. Stored copies below this number are treated as stale and the
    # FE banner re-prompts the user to re-confirm. The server stamps every
    # new POST /me/consent row with this value so the audit trail records
    # exactly which policy revision the user agreed to.
    consent_policy_version: int = Field(default=1, ge=1)
    # Salt mixed into the SHA-256 over the remote address before persisting
    # to ``user_consents.ip_address_hash``. Hashing the IP keeps the column
    # useful for "same source as last decision?" abuse detection without
    # storing raw PII; salting prevents a database leak from being trivially
    # reversed via a precomputed table. Required in staging/production
    # (enforced by ``_validate_for_environment``); dev defaults to a fixed
    # value so local runs are deterministic.
    ip_hash_salt: str | None = Field(default="dev-ip-hash-salt-change-me")

    # --- P0-4 give-up affordance ---
    # Soft-block window before the give-up affordance is available. ADR 0010
    # locks the default at 600 seconds (10 minutes); ops can override via the
    # GIVE_UP_MIN_SECONDS env var without a redeploy. A future per-mission
    # override (``mission.give_up_after_seconds`` in mission.yaml) would
    # read this as the global fallback.
    give_up_min_seconds: int = Field(default=600, ge=0)

    # --- P0-6 account self-service ---
    # Number of days a generated data export remains downloadable before
    # the signed URL expires and the row flips to ``expired``. Capped at 30
    # so signed URLs don't outlive a user's "I changed my mind" window —
    # short TTLs are safer.
    data_export_ttl_days: int = Field(default=7, ge=1, le=30)
    # Number of days between ``POST /me/delete`` and the worker's hard-
    # delete pass. The user can cancel at any point during the window via
    # ``POST /me/delete/cancel``. Long enough to forgive accidental clicks,
    # short enough to honour "right to be forgotten" promptly.
    account_deletion_grace_days: int = Field(default=7, ge=1, le=30)
    # Email domain stamped onto tombstone rows when the deletion worker
    # hard-deletes an account. MUST be a domain you control + intentionally
    # cannot receive mail (so the tombstone email cannot collide with a
    # real sign-up). The historical default matches the production
    # placeholder; staging / dev can override to keep environments from
    # ever sharing a tombstone identity.
    deleted_tombstone_domain: str = Field(
        default="deleted.openagentdojo.app",
        min_length=4,
    )
    # When True, the local driver runs ``setup_commands`` (e.g. ``pnpm install``)
    # at provision time. Default False because repo packs are pre-installed
    # (see plan §9.4); flip to True for any pack that ships *without* a
    # populated node_modules / .venv.
    arena_run_local_setup: bool = False

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
    # Verify server TLS certificates when STARTTLS is in play. Off by default
    # so local dev with MailHog (self-signed cert) keeps working; flipped on
    # automatically for staging/production via ``_validate_for_environment``.
    smtp_verify_certs: bool = False
    # Share-token JWT signing secret. Falls back to ``session_secret`` for dev
    # so an existing .env keeps working; staging/production should set their
    # own dedicated value.
    share_token_secret: str | None = None
    # P0-11 — verification envelope HMAC secret. Falls back to
    # ``share_token_secret`` then ``session_secret`` for dev so an existing
    # .env keeps working; staging/production MUST set this to a dedicated
    # value (validator below). The dedicated secret survives a session-
    # secret rotation so verification signatures on PDFs already in the
    # wild keep verifying for a year.
    verify_secret: str | None = None
    # P0-11 — soft cap on force re-renders per submission per day. The
    # default keeps a user from cycling the cached PDF; tune via env if
    # power users hit the limit legitimately.
    report_render_force_daily_cap: int = Field(default=5, ge=1, le=100)

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

    # Treat empty-string env values (``ARENA_RUN_LOCAL_SETUP=`` /
    # ``ALLOW_DEV_AUTH=``) as ``False``. Without this, pydantic raises
    # bool_parsing on an empty string and the whole app fails to boot —
    # which is what burned us during the M0 e2e launch (.env had a leftover
    # ``ALLOW_DEV_AUTH=`` and ``ARENA_RUN_LOCAL_SETUP=`` from when the value
    # was bridged through shell exports).
    @field_validator(
        "arena_run_local_setup",
        "allow_dev_auth",
        "provision_in_process",
        "feature_llm_narration",
        "smtp_start_tls",
        "smtp_verify_certs",
        mode="before",
    )
    @classmethod
    def _blank_bool_to_false(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return False
        return v

    # --- CORS ---
    # Optional comma-separated additional origins beyond ``web_origin``.
    # Useful when staging and prod share an API but live on different web
    # hosts. Wildcard ``*`` is allowed in development only — production /
    # staging must list each origin explicitly.
    cors_extra_origins: str = ""

    @property
    def cors_origins(self) -> list[str]:
        """Origins permitted to call the API."""
        origins: list[str] = []
        primary = self.web_origin or "http://localhost:3000"
        origins.append(primary)
        extras = (self.cors_extra_origins or "").strip()
        if extras:
            origins.extend(o.strip() for o in extras.split(",") if o.strip())
        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for origin in origins:
            if origin not in seen:
                seen.add(origin)
                deduped.append(origin)
        return deduped

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
    def _validate_for_environment(self) -> Settings:  # noqa: PLR0912 — flat list of fail-loud invariants; splitting would hide the surface
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

        # ---- share_token_secret: explicit + distinct from session_secret ----
        _validate_share_token_secret(self.share_token_secret, secret)

        # ---- verify_secret: explicit + distinct from session + share ----
        _validate_verify_secret(
            self.verify_secret, secret, self.share_token_secret or ""
        )

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

        # ---- SANDBOX_DRIVER=local is unsafe in staging/production ----
        # ``local`` shells out without container isolation. Operators must
        # opt in to ``docker`` explicitly outside development.
        if self.sandbox_driver != "docker":
            raise ValueError(
                "SANDBOX_DRIVER must be 'docker' in staging/production "
                "(the 'local' driver has no isolation)"
            )

        # ---- IP_HASH_SALT must be set + non-dev in staging/production ----
        # The consent-record path SHA-256s ``salt + remote_addr`` before
        # persisting; an empty or dev-prefixed salt would let an attacker
        # who lifts the DB precompute a rainbow table over the IPv4 space.
        salt = (self.ip_hash_salt or "").strip()
        if not salt or salt.startswith("dev-"):
            raise ValueError(
                "IP_HASH_SALT must be set to a non-dev value in staging/production "
                "(consent records hash the remote address with this salt; an empty "
                "or dev-prefixed value defeats the hash)"
            )
        if len(salt) < 32:
            raise ValueError(
                "IP_HASH_SALT must be >=32 chars in staging/production"
            )

        # ---- SMTP TLS verification must be on ----
        if self.smtp_host and self.smtp_host != "localhost" and not self.smtp_verify_certs:
            raise ValueError(
                "SMTP_VERIFY_CERTS must be true in staging/production "
                "(otherwise STARTTLS is open to passive MITM)"
            )

        return self


def _validate_verify_secret(
    raw: str | None, session_secret: str, share_secret: str
) -> None:
    """Enforce the staging/production verify-secret invariants (P0-11).

    Apart from the usual length / dev-prefix bounds, the verify secret
    must differ from both the session and share-token secrets — the
    whole point of a separate secret is that a session-secret rotation
    (which can happen on operational events) does NOT invalidate every
    issued verification signature, which would silently break PDFs
    already attached to résumés. Sharing the share-token secret has the
    same problem in reverse: rotating share tokens shouldn't ripple
    into the credentialing surface.
    """
    raw_secret = (raw or "").strip()
    if not raw_secret:
        raise ValueError(
            "VERIFY_SECRET must be set in staging/production "
            "(falling back to SESSION_SECRET / SHARE_TOKEN_SECRET would mean "
            "rotating either one silently invalidates every verification "
            "signature on PDFs already in the wild)"
        )
    if raw_secret.startswith("dev-"):
        raise ValueError("VERIFY_SECRET must not start with 'dev-' in staging/production")
    if len(raw_secret) < 32:
        raise ValueError("VERIFY_SECRET must be >=32 chars in staging/production")
    if raw_secret == session_secret:
        raise ValueError(
            "VERIFY_SECRET must differ from SESSION_SECRET in staging/production"
        )
    if share_secret and raw_secret == share_secret:
        raise ValueError(
            "VERIFY_SECRET must differ from SHARE_TOKEN_SECRET in staging/production"
        )


def _validate_share_token_secret(raw: str | None, session_secret: str) -> None:
    """Enforce the staging/production share-token secret invariants (P2-B12).

    Sharing one secret across two unrelated token types (login session vs.
    public report share link) means a compromise of one rotates both — an
    operator who thought they were resetting login sessions would also
    silently invalidate every active share link, and vice versa. Pulled out
    of ``_validate_for_environment`` so that method stays under the linter's
    branch budget.
    """
    share_secret = (raw or "").strip()
    if not share_secret:
        raise ValueError(
            "SHARE_TOKEN_SECRET must be set in staging/production "
            "(falling back to SESSION_SECRET conflates two unrelated trust domains)"
        )
    if share_secret == session_secret:
        raise ValueError("SHARE_TOKEN_SECRET must differ from SESSION_SECRET in staging/production")
    if len(share_secret) < 32:
        raise ValueError("SHARE_TOKEN_SECRET must be >=32 chars in staging/production")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton settings instance."""
    return Settings()
