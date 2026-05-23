"""User account model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at, nullable_ts, uuid_pk


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    handle: Mapped[str | None] = mapped_column(CITEXT(), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    github_login: Mapped[str | None] = mapped_column(String(120), nullable=True)
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

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email!s}>"
