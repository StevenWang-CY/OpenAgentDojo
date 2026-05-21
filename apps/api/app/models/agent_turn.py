"""One agent prompt → response cycle."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at, nullable_ts, uuid_pk


class AgentTurn(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_index", name="agent_turns_session_turn_uq"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    selected_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    agent_response: Mapped[str] = mapped_column(Text, nullable=False)
    applied_patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    patch_applied_at: Mapped[datetime | None] = nullable_ts()
    created_at: Mapped[datetime] = created_at()
