"""User account model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at, nullable_ts, uuid_pk


class User(Base):
    __tablename__ = "users"
    # Phase 4.A.18 — mirror migration 0021's CHECK on the ORM so an
    # ``alembic revision --autogenerate`` against a freshly-baselined
    # schema doesn't try to drop the (still legitimately-present)
    # constraint. The CHECK enforces the invariant that the two
    # GitHub-verified indicators move together: a row with a
    # ``github_id`` MUST also carry a ``github_verified_at``, and vice
    # versa. Without this, a code path could leave one column populated
    # and the other not, silently confusing the FE verified-badge
    # rendering.
    __table_args__ = (
        CheckConstraint(
            "(github_id IS NULL) = (github_verified_at IS NULL)",
            name="users_github_verified_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    handle: Mapped[str | None] = mapped_column(CITEXT(), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # P0-7 — GitHub OAuth verification columns.
    #
    # ``github_login`` is the user's current GitHub handle (free-form,
    # NULLable, NOT unique — github recycles abandoned handles). It is a
    # display hint; do NOT use it as the join key. ``github_id`` is the
    # authoritative identity (numeric, immutable, unique) and the only
    # column the OAuth callback upserts on. The pair
    # (``github_id``, ``github_verified_at``) is constrained by migration
    # 0021's CHECK to move together: a verified badge can never render
    # without an underlying GitHub identity, and vice versa.
    github_login: Mapped[str | None] = mapped_column(String(120), nullable=True)
    github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    github_avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_html_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_verified_at: Mapped[datetime | None] = nullable_ts()
    created_at: Mapped[datetime] = created_at()
    last_login_at: Mapped[datetime | None] = nullable_ts()
    # P0-1 — tutorial progress. ``tutorial_completed_at`` is NULL for a
    # never-completed user (the catalog renders the // start here banner
    # off this); set to the completion timestamp once Mission 00 is
    # submitted. ``tutorial_replay_count`` is internal telemetry for
    # content tuning and is never surfaced publicly.
    tutorial_completed_at: Mapped[datetime | None] = nullable_ts()
    tutorial_replay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # P0-6 — account self-service columns.
    #
    # ``pending_email`` holds the address the user is migrating to between
    # ``POST /me/email/change`` (which sets it) and ``POST /me/email/confirm``
    # (which lands it). The unique constraint lives at the application level —
    # ``POST /me/email/change`` rejects 409 if the target collides with any
    # other account's ``email`` or ``pending_email``.
    pending_email: Mapped[str | None] = mapped_column(CITEXT(), nullable=True)
    # ``deletion_scheduled_at`` is the wall-clock moment the user's account
    # will be hard-deleted (7 days after ``POST /me/delete``). While non-NULL
    # the deletion-lock middleware returns 403 for every mutating endpoint
    # except ``/me/delete/cancel``. Cleared by cancel; cleared by the
    # ``process_deletion_grace`` worker after the row is tombstoned.
    deletion_scheduled_at: Mapped[datetime | None] = nullable_ts()
    # ``session_epoch`` is the per-user "sign out everywhere" cursor. Cookies
    # mint with ``claim.epoch = user.session_epoch``; verification rejects
    # whenever ``claim.epoch < user.session_epoch``. Bumping the epoch is the
    # only mechanism for invalidating cookies issued before a given moment
    # without iterating every live JTI. Defaults to 1 (>0 lets the verifier
    # treat a missing claim as a downgrade attack and reject it).
    session_epoch: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # P1-4 (§"Coaching reflection" → "Privacy & data flow") — per-user
    # opt-out for the scratchpad coaching reflection. When False the
    # coaching endpoint short-circuits and returns ``reflection=null``
    # WITHOUT ever forwarding the scratchpad text to Bedrock. Defaults
    # to True for both new and backfilled accounts (mirrors the
    # analytics-by-default-after-consent posture from P0-5). The toggle
    # is surfaced on the ``/account/privacy`` page; flipping it does
    # NOT delete existing cache rows (the row is keyed by content hash,
    # not user id) — the cache cleanup happens at account-delete time
    # via the deletion worker (Wave 2D).
    coaching_reflections_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email!s}>"
