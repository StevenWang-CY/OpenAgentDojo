"""User attempt at a mission — pairs a DB row with an ephemeral sandbox."""

from __future__ import annotations

import uuid
from datetime import datetime

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
        # Plan §6.1: index on (user_id, started_at DESC) for "latest sessions" lookups.
        Index("idx_sessions_user", "user_id", desc("started_at")),
        # Mirror migration 0006 — keeps alembic --autogenerate clean.
        Index("idx_sessions_last_activity", "last_activity_at"),
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

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SessionRow {self.id} mission={self.mission_id} status={self.status}>"
