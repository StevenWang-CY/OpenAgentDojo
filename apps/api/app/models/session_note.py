"""Per-session scratchpad note (P1-4).

One row per session — the workspace's free-form markdown scratchpad. The
body is capped at 32 KB by both a Postgres CHECK constraint (defence in
depth, see migration 0028) and the API-layer 413 enforcement in
``apps/api/app/sessions/notes.py``.

Key design choices (see [P1_DESIGN.md §P1-4](../../../P1_DESIGN.md)):

* The row is implicit: a fresh session has no ``session_notes`` row at all
  — ``GET /sessions/{id}/note`` returns ``body=""`` without writing.
* ``updated_at`` is the wall-clock moment of the last successful PUT,
  used to compute the ``seconds_since_last_edit`` payload on the
  coalesced ``note.edited`` supervision event.
* ``ON DELETE CASCADE`` from sessions means GDPR account deletion (and
  the test-fixture session-teardown path) cleans up note rows for free —
  no parallel work in ``apps/api/app/workers/account_deletion.py``.
* The note text is deliberately NOT inlined into any supervision event
  payload — see the privacy note in P1_DESIGN.md §P1-4. Downstream
  artefacts (data export, replay) handle the body through this table
  exclusively.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SessionNote(Base):
    """The single ``session_notes`` row backing one session's scratchpad."""

    __tablename__ = "session_notes"
    __table_args__ = (
        # Belt to the API-layer 413's suspenders. Uses ``octet_length`` so
        # the cap is in **bytes** (matches the API enforcement, which
        # counts UTF-8 bytes via ``len(body.encode("utf-8"))``). The
        # original ``length(body)`` constraint counted Postgres characters,
        # which lets a 32 768-character payload sneak past at up to 4x the
        # intended byte budget once multi-byte glyphs land. Migration
        # 0033 (``0033_session_notes_byte_check``) swaps the live
        # constraint on Postgres; SQLite is reached via
        # ``Base.metadata.create_all`` in tests and does not understand
        # ``octet_length`` as a built-in — but the CHECK is a no-op on
        # SQLite anyway, so the API-level 413 guard in
        # ``apps/api/app/sessions/notes.py`` is the sole backstop in
        # tests (and is covered by a regression test).
        CheckConstraint(
            "octet_length(body) <= 32768",
            name="session_notes_body_length",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SessionNote session_id={self.session_id} bytes={len(self.body.encode('utf-8'))}>"
