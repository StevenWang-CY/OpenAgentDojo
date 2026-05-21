"""Shared ORM column helpers — keep model files free of boilerplate."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


def uuid_pk() -> Mapped[uuid.UUID]:
    """Standard UUID primary key column backed by Postgres `gen_random_uuid()`."""
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )


def created_at() -> Mapped[datetime]:
    """`TIMESTAMPTZ` column defaulting to `now()`."""
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def nullable_ts() -> Mapped[datetime | None]:
    """`TIMESTAMPTZ` column that may be null."""
    return mapped_column(DateTime(timezone=True), nullable=True)
