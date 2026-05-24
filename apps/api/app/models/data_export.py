"""User data-export job rows (P0-6).

Each row is the lifecycle of one ``POST /me/data-export`` request:

    queued → running → ready
                    ↘ failed
    ready → expired   (set lazily on read or by a scheduled sweeper)

Concurrency: the "one in flight per user" guarantee is enforced two ways:

1. Application layer — ``POST /me/data-export`` checks for any
   ``queued``/``running`` row before insert and returns 409 on collision.
2. Postgres partial unique index — defence-in-depth in case the route is
   bypassed (CLI tool, replay). The index is *not* present on SQLite
   because its DDL silently ignores ``WHERE`` on ``CREATE UNIQUE INDEX``,
   which would incorrectly reject a second *completed* export. Tests run
   on SQLite and rely on the route's check.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import uuid_pk

# Status enum values — referenced by the route, worker, and tests so a
# typo doesn't silently break the lifecycle. Mirrors the CHECK constraint
# below; the migration's constraint is the source of truth at the DB level.
EXPORT_STATUS_QUEUED: Final = "queued"
EXPORT_STATUS_RUNNING: Final = "running"
EXPORT_STATUS_READY: Final = "ready"
EXPORT_STATUS_FAILED: Final = "failed"
EXPORT_STATUS_EXPIRED: Final = "expired"

EXPORT_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {EXPORT_STATUS_READY, EXPORT_STATUS_FAILED, EXPORT_STATUS_EXPIRED}
)
EXPORT_IN_FLIGHT_STATUSES: frozenset[str] = frozenset(
    {EXPORT_STATUS_QUEUED, EXPORT_STATUS_RUNNING}
)


class DataExport(Base):
    """One row per user data-export request."""

    __tablename__ = "data_exports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','ready','failed','expired')",
            name="data_exports_status_check",
        ),
        Index("idx_data_exports_user", "user_id", desc("requested_at")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DataExport {self.id} user={self.user_id} status={self.status}>"
