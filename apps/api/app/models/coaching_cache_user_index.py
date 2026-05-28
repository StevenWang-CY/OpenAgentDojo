"""Link table: which ``llm_cache`` rows were produced by which user (P1-4).

The scratchpad coaching feature reads user-private text (the
``session_notes.body``) and forwards it to Bedrock. The cached output
goes into the shared ``llm_cache`` table keyed by ``content_hash``,
which is a SHA-256 of a payload that includes the notes hash — so the
cache row itself is content-addressed and a deletion worker has no
direct way to identify "this row belongs to user X" without
reconstructing every input payload.

Rather than ship that brittle recomputation, we stamp a lightweight
link here at generation time. The deletion worker (and the account
data-export worker) JOIN this table to ``llm_cache`` and act on rows
the user produced. The PK is (user_id, llm_cache_id) so re-stamping
the same row for the same user is a no-op (idempotent on retry / cache
hit re-generation).

``ON DELETE CASCADE`` chains from both ``users`` and ``llm_cache``:

* deleting a user wipes their link rows;
* deleting a cache row wipes the index entries pointing at it.

The order in the deletion worker matters — see
:func:`app.workers.account_deletion._hard_delete_user`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CoachingCacheUserIndex(Base):
    """Link (user_id, llm_cache_id) for scratchpad_coaching rows."""

    __tablename__ = "coaching_cache_user_index"
    __table_args__ = (
        Index("idx_coaching_cache_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    llm_cache_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("llm_cache.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CoachingCacheUserIndex user_id={self.user_id} "
            f"llm_cache_id={self.llm_cache_id}>"
        )
