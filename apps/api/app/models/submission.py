"""Final submission + grading payload."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at, uuid_pk


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("session_id", name="submissions_session_uq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    final_diff: Mapped[str] = mapped_column(Text, nullable=False)
    visible_test_results: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    hidden_test_results: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validator_results: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    score_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at()
