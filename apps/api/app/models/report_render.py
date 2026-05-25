"""Report render artifacts (P0-11).

One row per ``(submission_id, kind)`` lifecycle:

    queued → running → ready
                    ↘ failed

A force re-render (``POST /reports/{id}/render``) flips the row back to
``queued`` and overwrites ``s3_key`` once the worker completes — the row
identity is stable across re-renders so the FE's poll URL never has to
chase a new id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import uuid_pk

# Status enum — referenced by the route, worker, and tests. Mirrors the
# CHECK constraint in migration 0019; the DB constraint is canonical.
RENDER_STATUS_QUEUED: Final = "queued"
RENDER_STATUS_RUNNING: Final = "running"
RENDER_STATUS_READY: Final = "ready"
RENDER_STATUS_FAILED: Final = "failed"

RENDER_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {RENDER_STATUS_READY, RENDER_STATUS_FAILED}
)
RENDER_IN_FLIGHT_STATUSES: frozenset[str] = frozenset(
    {RENDER_STATUS_QUEUED, RENDER_STATUS_RUNNING}
)

# Kind enum.
RENDER_KIND_PDF: Final = "pdf"
RENDER_KIND_PNG: Final = "png"
RENDER_KINDS: frozenset[str] = frozenset({RENDER_KIND_PDF, RENDER_KIND_PNG})


class ReportRender(Base):
    """One render lifecycle row per (submission, kind) pair."""

    __tablename__ = "report_renders"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('pdf','png')",
            name="report_renders_kind_check",
        ),
        CheckConstraint(
            "status IN ('queued','running','ready','failed')",
            name="report_renders_status_check",
        ),
        UniqueConstraint(
            "submission_id",
            "kind",
            name="uq_report_renders_submission_kind",
        ),
        Index("idx_report_renders_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    submission_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ReportRender {self.id} submission={self.submission_id} "
            f"kind={self.kind} status={self.status}>"
        )
