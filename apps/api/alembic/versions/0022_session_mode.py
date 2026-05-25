"""Anti-cheating posture / proctored mode (P0-8).

Adds three columns:

* ``sessions.mode VARCHAR(20) NOT NULL DEFAULT 'self_study'`` — the session's
  anti-cheating posture. The two legal values are ``'self_study'`` (the
  default; banner says "honor mode — practice only, not a verified
  score") and ``'proctored'`` (opt-in per attempt at session create;
  enables window/document integrity event collection in the browser).
* ``sessions.integrity_signals_count INT NOT NULL DEFAULT 0`` — a rolling
  counter incremented every time the integrity endpoint accepts a
  signal on a proctored session. Surfaced on the workspace chip and the
  post-mortem walkthrough so a high count is legible.
* ``submissions.verified BOOLEAN NOT NULL DEFAULT FALSE`` — stamped by the
  grading runner from ``session.mode == 'proctored'`` at submit time.
  The verify envelope, the public profile radar (verified-only by
  default), and the report page badge all key off this column.

A CHECK constraint is added to ``sessions.mode`` to keep the small enum
honest at the DB layer; extending it (e.g. a future "tournament" mode)
requires bumping a new migration in lockstep with the schema change.

SQLite divergence
-----------------
Postgres receives the ``CHECK`` constraint; SQLite (used by the test
harness) also accepts CHECK syntax so the migration is dialect-uniform.
The default-value semantics are identical across both engines.

Revision ID: 0022_session_mode
Revises: 0020_session_reset_event
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_session_mode"
# Depend on 0020 — the parallel P0-7 migration (0021) lands separately;
# alembic resolves a fan-out by branch label. Pinning to 0020 keeps this
# migration's down_revision stable regardless of P0-7's merge order.
down_revision: str | None = "0020_session_reset_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # ---- sessions.mode -----------------------------------------------------
    op.add_column(
        "sessions",
        sa.Column(
            "mode",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'self_study'"),
        ),
    )
    # Phase 4.A.15 — SQLite cannot ADD a CHECK constraint in-place; it
    # must be added by recreating the table via ``batch_alter_table``.
    # Postgres handles it directly. The branch keeps the migration
    # working under both engines (the test harness runs on SQLite).
    if is_postgres:
        op.create_check_constraint(
            "sessions_mode_check",
            "sessions",
            "mode IN ('self_study', 'proctored')",
        )
    else:
        with op.batch_alter_table("sessions", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                "sessions_mode_check",
                "mode IN ('self_study', 'proctored')",
            )

    # ---- sessions.integrity_signals_count ----------------------------------
    op.add_column(
        "sessions",
        sa.Column(
            "integrity_signals_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # ---- submissions.verified ----------------------------------------------
    # Use a portable boolean default. Postgres + SQLite both accept the
    # literal ``false``; quoted-string defaults in pre-existing migrations
    # use ``sa.text("false")`` so we mirror that style.
    op.add_column(
        "submissions",
        sa.Column(
            "verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    if is_postgres:
        # Light operational comment so a ``\d+ sessions`` in psql tells the
        # next on-call where the mode column comes from.
        op.execute(
            "COMMENT ON COLUMN sessions.mode IS "
            "'P0-8 anti-cheating posture: self_study (default, honor mode) "
            "or proctored (verified). See app/sessions/integrity.py.'"
        )
        op.execute(
            "COMMENT ON COLUMN submissions.verified IS "
            "'P0-8 — true iff session.mode was proctored at submit time. "
            "Drives the public profile verified-only radar.'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("COMMENT ON COLUMN sessions.mode IS NULL")
        op.execute("COMMENT ON COLUMN submissions.verified IS NULL")

    op.drop_column("submissions", "verified")
    if is_postgres:
        op.drop_constraint("sessions_mode_check", "sessions", type_="check")
    else:
        with op.batch_alter_table("sessions", recreate="always") as batch_op:
            batch_op.drop_constraint("sessions_mode_check", type_="check")
    op.drop_column("sessions", "integrity_signals_count")
    op.drop_column("sessions", "mode")
