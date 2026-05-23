"""Add multi-attempt accounting columns (P0-3) + shared score_cap_reason (P0-4).

P0-3 (multi-attempt scoring policy):
  * ``sessions.attempt_index`` (INTEGER, default 1) — the 1-based ordinal of
    this attempt against ``(user_id, mission_id)``. Computed at create_session
    time and backfilled here by ROW_NUMBER() over the existing graded
    sessions.
  * ``sessions.previous_session_id`` (UUID, NULL) — when the new session was
    created as a deliberate "Retry mission" click, this points at the prior
    graded session. ``ON DELETE SET NULL`` so a P0-6 hard-delete of a prior
    attempt gracefully breaks the link without raising FK errors.

P0-4 (give-up cap) shares this migration's ``submissions.score_cap_reason``
column. Whichever migration ships first owns the column; the design doc
(`P0_DESIGN.md` §0.1) keeps the order stable:

  * ``submissions.score_cap_reason`` (TEXT, NULL) — when set, a post-grading
    rule capped the total. Currently only ``'gave_up'`` is allowed; the
    CHECK constraint pins the enum so a malformed write can't silently
    bypass the cap policy.

The migration is forward-only safe — every column carries a default, the
backfill is deterministic, and the downgrade drops in reverse.

Revision ID: 0013_multi_attempt
Revises: 0012_post_mortem_evidence
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_multi_attempt"
down_revision: Union[str, None] = "0012_post_mortem_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # sessions — multi-attempt accounting columns.
    op.add_column(
        "sessions",
        sa.Column(
            "attempt_index",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "previous_session_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "sessions_previous_session_fk",
        source_table="sessions",
        referent_table="sessions",
        local_cols=["previous_session_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_sessions_user_mission",
        "sessions",
        ["user_id", "mission_id"],
    )

    # Backfill attempt_index using ROW_NUMBER() over the user's graded
    # sessions for each mission. The ordering uses completed_at (preferred,
    # since the multi-attempt timeline is "in completion order") with
    # started_at as a tiebreaker for any rows whose completed_at is null
    # (legacy data, abandoned/error sessions that never completed).
    op.execute(
        sa.text(
            """
            WITH numbered AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_id, mission_id
                           ORDER BY
                               completed_at NULLS LAST,
                               started_at
                       ) AS n
                FROM sessions
            )
            UPDATE sessions s
            SET attempt_index = numbered.n
            FROM numbered
            WHERE s.id = numbered.id
              AND s.attempt_index = 1
              AND numbered.n > 1;
            """
        )
    )

    # submissions — score_cap_reason column (shared with 0014/P0-4).
    op.add_column(
        "submissions",
        sa.Column(
            "score_cap_reason",
            sa.Text(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "submissions_score_cap_reason_check",
        "submissions",
        "score_cap_reason IS NULL OR score_cap_reason IN ('gave_up')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "submissions_score_cap_reason_check",
        "submissions",
        type_="check",
    )
    op.drop_column("submissions", "score_cap_reason")
    op.drop_index("idx_sessions_user_mission", table_name="sessions")
    op.drop_constraint(
        "sessions_previous_session_fk",
        "sessions",
        type_="foreignkey",
    )
    op.drop_column("sessions", "previous_session_id")
    op.drop_column("sessions", "attempt_index")
