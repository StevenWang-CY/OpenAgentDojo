"""Auth-related response schemas.

Kept small and explicit so the public OpenAPI surface stays stable and the
frontend's generated types don't churn on every refactor.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WsTokenRead(BaseModel):
    """Response payload for ``GET /sessions/{id}/ws-token``."""

    token: str
    ttl_seconds: int


class ShareTokenRead(BaseModel):
    """Response payload for ``POST /reports/{id}/share``."""

    share_token: str
    share_url: str
    expires_at: datetime


class GithubOAuthAvailability(BaseModel):
    """Response payload for ``GET /auth/github/available`` (P0-7).

    The FE polls this on the sign-in page to decide whether to render the
    "Continue with GitHub" button. ``enabled`` is True only when both
    ``GITHUB_OAUTH_CLIENT_ID`` and ``GITHUB_OAUTH_CLIENT_SECRET`` are set on
    the backend; misconfigured deployments fall back to magic-link-only
    without showing the user a button that would 503.
    """

    model_config = ConfigDict(extra="forbid")
    enabled: bool


class GithubProfile(BaseModel):
    """Normalised view of a GitHub user fetched during OAuth callback (P0-7).

    The shape is the contract between ``app.auth.github_oauth.fetch_user_profile``
    (the network layer) and the callback route (the persistence layer).
    Tests stub the former and assert against the latter through this model
    so a future GitHub API shape change is caught at exactly one place.

    Fields mirror the persisted columns 1:1 (``github_id`` → ``users.github_id``,
    etc.) plus the ``email`` we use to upsert/merge the local row.
    """

    model_config = ConfigDict(extra="forbid")

    github_id: int
    login: str
    name: str | None = None
    avatar_url: str | None = None
    html_url: str
    email: str
