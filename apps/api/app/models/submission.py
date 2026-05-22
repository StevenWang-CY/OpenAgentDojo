"""Final submission + grading payload."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
    created_at: Mapped[datetime] = created_at()
