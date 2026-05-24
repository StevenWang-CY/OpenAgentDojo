"""Account self-service (P0-6) — pending email, deletion grace, data exports, session epoch.

Adds the schema P0-6 needs to let users change email, sign out everywhere,
export their data, and schedule a 7-day deletion grace — all without
contacting support.

  * ``users.pending_email`` (CITEXT, NULL) — the address the user is trying
    to migrate to. Cleared when ``/me/email/confirm`` lands the change.
  * ``users.deletion_scheduled_at`` (TIMESTAMPTZ, NULL) — when set, the
    deletion-lock middleware blocks every mutating endpoint except
    ``/me/delete/cancel`` until the grace expires (or the user cancels).
  * ``users.session_epoch`` (INTEGER, NOT NULL, DEFAULT 1) — bumped on
    "sign out everywhere", email confirm, deletion schedule, and hard-
    delete. Cookies carry an ``epoch`` claim; verification rejects when
    ``claim.epoch < user.session_epoch`` (per-user invalidation without
    iterating live JTIs).
  * ``magic_link_tokens.purpose`` (TEXT, NOT NULL, DEFAULT 'sign_in',
    CHECK IN ('sign_in','email_change')) — discriminator for the two
    one-time-token flows. We extend the existing table rather than add a
    sibling ``email_change_tokens`` because the lifecycle (hash storage,
    consume-once, 30-min expiry, revoke-on-reissue) is identical and the
    blast radius on the existing magic-link tests is one column.
  * ``data_exports`` table — one row per user export request.

Down-revision wiring
--------------------
The file number 0016 is reserved per P0_DESIGN §0.1, with 0015 owned by
Agent A's parallel P0-5 (user consents) branch. This revision chains off
``0015_user_consents`` so the linear history is preserved.

SQLite divergence
-----------------
The "one in-flight export per user" guarantee is enforced by a partial
unique index on Postgres. SQLite silently ignores the ``WHERE`` clause on
unique indexes (it treats it as a regular unique index, which would
incorrectly reject a *second* completed export). Tests therefore rely on
the application-layer check in ``POST /me/data-export`` instead — which
also runs on Postgres as defence-in-depth in case the index is briefly
absent during a migration.

Revision ID: 0016_account_self_service
Revises: 0015_user_consents
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

revision: str = "0016_account_self_service"
down_revision: str | None = "0015_user_consents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # ---- users: pending_email / deletion_scheduled_at / session_epoch ----
    op.add_column(
        "users",
        sa.Column(
            "pending_email",
            CITEXT() if is_postgres else sa.Text(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "deletion_scheduled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "session_epoch",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    # ---- magic_link_tokens: purpose ----
    op.add_column(
        "magic_link_tokens",
        sa.Column(
            "purpose",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'sign_in'"),
        ),
    )
    op.create_check_constraint(
        "magic_link_tokens_purpose_check",
        "magic_link_tokens",
        "purpose IN ('sign_in','email_change')",
    )

    # ---- data_exports ----
    op.create_table(
        "data_exports",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()") if is_postgres else None,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("bytes_total", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else None,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','ready','failed','expired')",
            name="data_exports_status_check",
        ),
    )

    op.create_index(
        "idx_data_exports_user",
        "data_exports",
        ["user_id", sa.text("requested_at DESC")],
    )

    # The "one in-flight per user" partial unique index is Postgres-only;
    # SQLite ignores ``WHERE`` on unique indexes (and would reject a second
    # *completed* export). The route layer also rejects defensively.
    if is_postgres:
        op.execute(
            "CREATE UNIQUE INDEX uq_data_exports_one_in_flight "
            "ON data_exports (user_id) "
            "WHERE status IN ('queued','running')"
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("DROP INDEX IF EXISTS uq_data_exports_one_in_flight")
    op.drop_index("idx_data_exports_user", table_name="data_exports")
    op.drop_table("data_exports")

    op.drop_constraint(
        "magic_link_tokens_purpose_check",
        "magic_link_tokens",
        type_="check",
    )
    op.drop_column("magic_link_tokens", "purpose")

    op.drop_column("users", "session_epoch")
    op.drop_column("users", "deletion_scheduled_at")
    op.drop_column("users", "pending_email")
