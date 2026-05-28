"""P1-4 — workspace scratchpad / session_notes table.

Provisions the persistent backing store for the per-session scratchpad
described in [P1_DESIGN.md §P1-4](../../../P1_DESIGN.md):

  * ``session_notes`` — one row per session (PK == session_id, ON
    DELETE CASCADE). ``body`` defaults to '' so the implicit "no
    scratchpad yet" state still round-trips through the upsert path.
  * Postgres CHECK constraint bounds ``length(body) <= 32768`` (32 KB).
    The API layer enforces the same cap with a 413 response so SQLite
    test paths (which silently ignore CHECK) still reject oversized
    bodies before the INSERT runs.

The new ``supervision_events.event_type`` strings ``note.edited`` and
``note.viewed_during_prompt`` need no schema migration — the
``supervision_events`` table stores ``event_type`` as ``String(60)``
and ``payload`` as JSONB, so the existing shape accepts both new
event kinds. The validator schema + TS types are updated alongside
this migration (see ``docs/schemas/event.schema.json`` and
``packages/shared-types/src/events.ts``).

Migration ordering
------------------
``down_revision = "0027_recommendation_cache_extras"``. The
``0029_replay_artifact_index`` and ``0030_llm_cache`` migrations being
authored in parallel are expected to chain after this one once they
land (their down_revision will set to ``0028_session_notes`` to keep
``alembic heads`` linear).

SQLite divergence
-----------------
SQLite is reached via ``Base.metadata.create_all`` in
``tests/conftest.py`` (not Alembic), so this migration is a no-op
under tests. The ORM model in ``apps/api/app/models/session_note.py``
defines the same shape and the body-length CHECK constraint is
declared at the ORM layer so create_all emits it too — SQLite simply
ignores it at insert time, which is why the API-level 413 guard is
the load-bearing test for oversized bodies in the test suite.

Revision ID: 0028_session_notes
Revises: 0027_recommendation_cache_extras
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028_session_notes"
down_revision: str | None = "0027_recommendation_cache_extras"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_notes",
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "body",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "length(body) <= 32768",
            name="session_notes_body_length",
        ),
    )


def downgrade() -> None:
    op.drop_table("session_notes")
