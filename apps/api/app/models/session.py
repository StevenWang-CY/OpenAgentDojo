"""User attempt at a mission — pairs a DB row with an ephemeral sandbox."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import nullable_ts, uuid_pk


class SessionRow(Base):
    """The DB row representing one Session.

    Named ``SessionRow`` (not ``Session``) to avoid clashing with FastAPI's
    and SQLAlchemy's own ``Session`` types. The table is still ``sessions``.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('provisioning','active','submitting','graded','abandoned','error')",
            name="sessions_status_check",
        ),
        CheckConstraint(
            "score IS NULL OR (score BETWEEN 0 AND 100)",
            name="sessions_score_range",
        ),
        # P0-8 — anti-cheating posture. Mirrors migration 0022.
        CheckConstraint(
            "mode IN ('self_study', 'proctored')",
            name="sessions_mode_check",
        ),
        # Plan §6.1: index on (user_id, started_at DESC) for "latest sessions" lookups.
        Index("idx_sessions_user", "user_id", desc("started_at")),
        # Mirror migration 0006 — keeps alembic --autogenerate clean.
        Index("idx_sessions_last_activity", "last_activity_at"),
        # Mirror migration 0013 — composite index supports the
        # multi-attempt aggregations on ``(user_id, mission_id)`` (your_attempts
        # strip on the mission detail page; best-per-mission profile dedupe).
        Index("idx_sessions_user_mission", "user_id", "mission_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    mission_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # Stamped by the sandbox pool on every driver I/O call. Powers the idle
    # reaper (see ``app.sandbox.pool``).
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = nullable_ts()
    sandbox_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # P0-3 — 1-based ordinal of this attempt against (user_id, mission_id).
    # Set at create_session time so the multi-attempt aggregations on the
    # mission detail page never have to re-derive it from the row order.
    attempt_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=sa.text("1"),
    )
    # P0-3 — when set, the new session was created via the "Retry mission"
    # CTA on the report page. Pointer back to the prior session so an
    # operator can trace the chain of attempts. ON DELETE SET NULL so a
    # P0-6 hard-delete of an earlier attempt gracefully breaks the link.
    previous_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    # P0-4 — when set, the user invoked the give-up affordance. The grading
    # runner reads this flag at score-time and applies a 50/100 total cap
    # (recorded in ``submissions.score_cap_reason``). NULL means "submitted
    # normally"; the timestamp is the wall-clock moment the user clicked.
    gave_up_at: Mapped[datetime | None] = nullable_ts()
    # P0-8 — anti-cheating posture. ``'self_study'`` (default) shows the
    # honor-mode banner and silently drops integrity events; ``'proctored'``
    # enables browser-side window/document signal collection, increments
    # ``integrity_signals_count`` on every accepted event, and stamps
    # ``submissions.verified = True`` at grade time. Set once at session
    # create — there is no mid-session promotion path because flipping the
    # toggle after work has begun would make the verified flag meaningless.
    mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="self_study",
        server_default=sa.text("'self_study'"),
    )
    # P0-8 — rolling counter incremented every time the integrity endpoint
    # persists a signal on this session. Self-study sessions never
    # increment this. Surfaces on the proctored chip in WorkspaceTopBar and
    # on the post-mortem walkthrough so a recruiter (or the user) can see
    # "the proctored attempt was clean — 0 integrity signals" at a glance.
    integrity_signals_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SessionRow {self.id} mission={self.mission_id} "
            f"status={self.status} mode={self.mode}>"
        )
