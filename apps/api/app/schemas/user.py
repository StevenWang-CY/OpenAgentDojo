"""User-facing schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# Maximum display-name length (matches ``users.display_name`` String(120)).
DISPLAY_NAME_MAX_LEN = 120


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    handle: str | None = None
    display_name: str | None = None
    github_login: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None
    # ``/auth/me`` always sets a CSRF token (either the existing cookie or a
    # freshly minted one — see ``_build_me_response``). The schema reflects
    # that: making it required prevents FE call sites from defensively
    # branching on a value that the BE guarantees.
    csrf_token: str
    # P0-1 — when set, the user has finished Mission 00 at least once. The
    # catalog's "// start here" banner renders ONLY when this is null; the
    # "Replay tutorial" entry in the header dropdown re-clears this server-
    # side and bumps ``tutorial_replay_count``.
    tutorial_completed_at: datetime | None = None
    # P0-1 — incremented every time the user re-runs the tutorial. Surfaced
    # to the FE so the "// orientation · completed YYYY-MM-DD · replay" row
    # can show "(replayed Nx)" once the count is non-zero. Internal-only
    # telemetry that never makes it to public profile surfaces.
    tutorial_replay_count: int = 0
    # P0-6 — pending email change. When set, ``/me`` renders a "confirm via
    # the link we sent" banner. Cleared by ``/me/email/confirm``.
    pending_email: EmailStr | None = None
    # P0-6 — when set, the user has initiated a 7-day deletion grace. The
    # FE Danger tab renders the countdown + cancel button from this field.
    # The deletion-lock middleware blocks every mutating endpoint except
    # ``/me/delete/cancel`` while this is non-null.
    deletion_scheduled_at: datetime | None = None


class DisplayNameUpdate(BaseModel):
    """Body for ``PATCH /me``.

    ``handle`` is intentionally absent: per P0_DESIGN §P0-6 ("Open
    decisions" → "Handle changes"), the public profile URL is stable, so
    we reject handle changes at the schema layer rather than rejecting at
    the route layer (which gives the FE a typed error instead of a 422).
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(
        default=None,
        description="New display name (≤120 chars, no leading/trailing whitespace).",
    )

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if stripped != v:
            raise ValueError("display_name must not have leading or trailing whitespace")
        if len(v) == 0:
            raise ValueError("display_name must not be empty")
        if len(v) > DISPLAY_NAME_MAX_LEN:
            raise ValueError(
                f"display_name must be at most {DISPLAY_NAME_MAX_LEN} characters"
            )
        return v


class EmailChangeRequest(BaseModel):
    """Body for ``POST /me/email/change``."""

    model_config = ConfigDict(extra="forbid")
    new_email: EmailStr


class EmailChangeConfirm(BaseModel):
    """Body for ``POST /me/email/confirm`` — the raw magic-link token."""

    model_config = ConfigDict(extra="forbid")
    token: str = Field(..., min_length=16, max_length=512)


class DeleteAccountRequest(BaseModel):
    """Body for ``POST /me/delete`` — re-type the email to confirm intent."""

    model_config = ConfigDict(extra="forbid")
    confirm_email: EmailStr


class DataExportStatus(BaseModel):
    """Status enum mirror used in OpenAPI for FE typing."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["queued", "running", "ready", "failed", "expired"]


class DataExportRead(BaseModel):
    """Serialised :class:`app.models.data_export.DataExport`.

    ``download_url`` is populated by the route only when the export is in
    ``ready`` state AND has not yet expired — a presigned URL signed in
    real time so the URL's lifetime cannot outlive the export's lifetime.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: Literal["queued", "running", "ready", "failed", "expired"]
    requested_at: datetime
    ready_at: datetime | None = None
    expires_at: datetime | None = None
    error: str | None = None
    bytes_total: int | None = None
    download_url: str | None = None


class DeletionScheduledRead(BaseModel):
    """Response body for ``POST /me/delete``."""

    model_config = ConfigDict(extra="forbid")
    scheduled_for: datetime


class DeletionLockError(BaseModel):
    """403 envelope returned by :class:`DeletionLockMiddleware`.

    Pydantic model so the OpenAPI surface declares the exact shape every
    mutating P0-6 endpoint may return while the account is mid-grace. The
    FE keys off ``code == 'deletion_scheduled'`` to render the "your
    account is scheduled for deletion" banner instead of a generic 403.

    ``scheduled_for`` is included so the FE can render the same countdown
    used on the ``/account`` page without re-fetching ``/auth/me``.
    """

    model_config = ConfigDict(extra="forbid")

    detail: str = Field(
        ...,
        description=(
            "Human-readable detail string. The FE keys off ``code`` for "
            "routing decisions; ``detail`` is only surfaced to support."
        ),
    )
    code: Literal["deletion_scheduled"]
    scheduled_for: datetime
