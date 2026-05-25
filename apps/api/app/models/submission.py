"""Final submission + grading payload."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at, uuid_pk


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("session_id", name="submissions_session_uq"),
        # Mirror the indexes added in migrations 0004/0008 so alembic
        # --autogenerate stays clean (was previously flagging these as drift).
        Index("idx_submissions_session", "session_id"),
        Index("idx_submissions_created", "created_at"),
        # P0-3/P0-4 — mirror migration 0013's CHECK so a malformed direct
        # SQL write can't sneak past the small enum. The only currently
        # legal value is ``'gave_up'``; future score-cap reasons (e.g. a
        # forfeit / disqualification) will extend this set + bump the
        # migration in lockstep.
        CheckConstraint(
            "score_cap_reason IS NULL OR score_cap_reason IN ('gave_up')",
            name="submissions_score_cap_reason_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    final_diff: Mapped[str] = mapped_column(Text, nullable=False)
    # JSONB column at the DB layer; on write the grading runner produces
    # lists (one entry per suite/validator) so the shape matches
    # ``packages/shared-types/src/api.ts``. ``dict`` is retained in the
    # type union for backwards compatibility with legacy rows that were
    # persisted before the contract switch (read paths must tolerate both).
    visible_test_results: Mapped[list[dict[str, Any]] | dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    hidden_test_results: Mapped[list[dict[str, Any]] | dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    validator_results: Mapped[list[dict[str, Any]] | dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    score_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # Anchor to the exact mission manifest that was graded. Allows replay/
    # audit to detect drift between the on-disk manifest at grade time and
    # the manifest the catalog DB row currently points at.
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # P0-2 — deterministic critical-moment list computed by
    # ``app.grading.diagnostics.compute_critical_moments``. Persisted in its
    # own column rather than buried in ``score_report`` so a replay can diff
    # just the moments without re-deserialising the whole report.
    critical_moments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # P0-3 / P0-4 — when set, a post-grading rule capped the total. The
    # dimension scores themselves remain honest; only ``total_score`` and
    # the report's ``total`` reflect the cap. Currently the only legal
    # value is ``'gave_up'`` (capped at 50/100 by the give-up affordance).
    # NULL means "no cap applied" — the public-profile aggregations exclude
    # capped attempts when any non-capped attempt exists on the same
    # mission (see ``app.profiles.router._best_per_mission``).
    score_cap_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # P0-11 — verification envelope hash + HMAC signature.
    # The runner stamps these at grade time from the canonical envelope in
    # ``app.reports.verification.build_envelope``. NULL only when grading
    # ran before P0-11 landed AND the backfill hasn't been re-run; the
    # public ``/verify/{id}`` endpoint 404s in that case to keep the
    # credentialing surface honest.
    verification_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    # P0-8 — true iff the producing session was proctored at submit time.
    # The grading runner copies ``session.mode == 'proctored'`` here, the
    # verify envelope reads this flag, and the public profile filters its
    # radar averages on it (honor-mode scores are practice, not credentials).
    verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa.text("false"),
    )
    created_at: Mapped[datetime] = created_at()
