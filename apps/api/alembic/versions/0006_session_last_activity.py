"""Add sessions.last_activity_at for the idle reaper.

The pool stamps every driver I/O call (run/read/write/apply/attach) onto an
in-memory ``handle.driver_state['last_activity_at']``. The DB column lets the
reaper survive an API restart — on warm start we backfill from this column
and resume idle accounting from the persisted timestamp.

Revision ID: 0006_session_last_activity
Revises: 0005_user_handle
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_session_last_activity"
down_revision: Union[str, None] = "0005_user_handle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Backfill explicitly so existing rows get a sensible value even on DBs
    # that don't honour the server default for existing rows.
    op.execute("UPDATE sessions SET last_activity_at = COALESCE(started_at, now())")
    op.create_index(
        "idx_sessions_last_activity",
        "sessions",
        ["last_activity_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_sessions_last_activity", table_name="sessions")
    op.drop_column("sessions", "last_activity_at")
