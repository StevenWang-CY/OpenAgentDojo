"""Index supervision_events for ``session.reset`` (P0-12).

The new ``session.reset`` event lands on the existing ``supervision_events``
table — no new columns, no new tables. The score engine + the post-mortem
walkthrough both count reset events per session, so a partial index keeps
those reads cheap as session histories grow.

Also leaves a ``COMMENT ON TABLE`` pointer at the canonical event-type
catalogue so a future operator running ``\\d+ supervision_events`` finds
the schema source of truth without grepping the codebase.

SQLite divergence
-----------------
``CREATE INDEX … WHERE`` is honoured by both Postgres and SQLite, but the
``COMMENT ON TABLE`` is Postgres-only. The migration branches on
``bind.dialect.name`` and skips the comment on SQLite. The test harness
patches models for SQLite via ``conftest._patch_models_for_sqlite`` so the
plain composite index also runs there as a back-stop.

Revision ID: 0020_session_reset_event
Revises: 0019_report_verification
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0020_session_reset_event"
down_revision: str | None = "0019_report_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Partial index covering session.reset lookups. Both engines respect
    # the WHERE clause so the index is small even on a busy table; the
    # planner picks it for "give me every reset on this session" queries
    # the diagnostics module issues per grade.
    if is_postgres:
        op.create_index(
            "idx_events_session_reset",
            "supervision_events",
            ["session_id"],
            postgresql_where=text("event_type = 'session.reset'"),
        )
        op.execute(
            "COMMENT ON TABLE supervision_events IS "
            "'Append-only log. Canonical event_type catalogue lives in "
            "docs/schemas/event.schema.json'"
        )
    else:
        # SQLite: composite index without the WHERE — still selective
        # enough for the test harness's small datasets and avoids the
        # dialect-specific syntax.
        op.create_index(
            "idx_events_session_reset",
            "supervision_events",
            ["session_id", "event_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_index("idx_events_session_reset", table_name="supervision_events")
    if is_postgres:
        # The COMMENT is metadata only — clearing it just sets the empty
        # string, which is the Postgres default.
        op.execute("COMMENT ON TABLE supervision_events IS NULL")
