"""File-edit event — emitted by the agent, the user, or a revert."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at, uuid_pk


class FileChange(Base):
    __tablename__ = "file_changes"
    __table_args__ = (
        CheckConstraint(
            "source IN ('agent','user','revert')",
            name="file_changes_source_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    hunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    added_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    removed_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at()
