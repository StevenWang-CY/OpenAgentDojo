"""GitHub OAuth identity verification (P0-7).

Adds the columns ``users`` needs to surface a verified-via-GitHub badge on
public profiles. The existing ``github_login`` column (added in an earlier
migration as a free-form hint) is now joined by three OAuth-authoritative
fields plus a verification timestamp:

  * ``github_id`` (BIGINT, NULL, UNIQUE) — GitHub's immutable numeric user
    id. Distinct from ``github_login`` (which the user can rename) so a
    handle change on github.com does NOT orphan our row. UNIQUE so a
    single GitHub identity can only attach to one OpenAgentDojo account.
  * ``github_avatar_url`` (TEXT, NULL) — the user's avatar at the moment of
    the most-recent successful OAuth callback. Refreshed on every callback.
  * ``github_html_url`` (TEXT, NULL) — the user's GitHub profile URL. The
    public-profile badge links here so consumers can independently verify
    the identity (one click off the credential).
  * ``github_verified_at`` (TIMESTAMPTZ, NULL) — wall-clock of the most-
    recent successful OAuth round-trip. The FE renders the "verified ·
    github · @login" chip ONLY when this is non-null. NULL implies the
    historical "email-only" path (which the FE labels "self-attested").

The CHECK constraint ``(github_id IS NULL) = (github_verified_at IS NULL)``
makes the verified badge unambiguous: a row with ``github_id`` must also
carry a ``github_verified_at``, and vice versa. The FE therefore only has
to branch on one of the two — drift between them would have been a silent
"we have an id but no proven verification" bug.

Indexes
-------
  * ``ix_users_github_id`` (UNIQUE) — primary lookup for the OAuth callback
    upsert path (``SELECT … WHERE github_id = $1``).
  * ``ix_users_github_login`` (NON-UNIQUE) — historical github handles
    DO collide (github recycles abandoned handles). Keep the index for
    operator queries but never enforce uniqueness; ``github_id`` is the
    authoritative join key.

SQLite divergence
-----------------
``BigInteger`` is honoured by both Postgres and SQLite. ``CHECK`` is also
universal. The Postgres-only branches concern column types only.

Revision ID: 0021_github_oauth
Revises: 0020_session_reset_event
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_github_oauth"
down_revision: str | None = "0020_session_reset_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.add_column(
        "users",
        sa.Column("github_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("github_avatar_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("github_html_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "github_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Unique index on github_id so a single GitHub identity can never attach
    # to two OpenAgentDojo accounts. The OAuth callback path SELECTs by this
    # column on every successful round-trip; the unique constraint is also
    # a hard merge-not-duplicate guarantee at the DB layer.
    op.create_index(
        "ix_users_github_id",
        "users",
        ["github_id"],
        unique=True,
    )

    # NON-unique index on github_login — github recycles abandoned handles so
    # a UNIQUE constraint would break the next user who legitimately
    # acquires the freed name. The index is only here for operator queries.
    op.create_index(
        "ix_users_github_login",
        "users",
        ["github_login"],
        unique=False,
    )

    # CHECK that the two "verified" indicators move together. Without this
    # a code path could leave one column populated and the other not, which
    # would silently confuse the FE badge rendering (the column it branches
    # on would disagree with the column it displays).
    if is_postgres:
        op.execute(
            "ALTER TABLE users ADD CONSTRAINT users_github_verified_check "
            "CHECK ((github_id IS NULL) = (github_verified_at IS NULL))"
        )
    else:
        # SQLite cannot ADD CONSTRAINT in place — recreate via batch.
        with op.batch_alter_table("users", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                "users_github_verified_check",
                "(github_id IS NULL) = (github_verified_at IS NULL)",
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_github_verified_check")
    else:
        with op.batch_alter_table("users", recreate="always") as batch_op:
            batch_op.drop_constraint("users_github_verified_check", type_="check")

    op.drop_index("ix_users_github_login", table_name="users")
    op.drop_index("ix_users_github_id", table_name="users")
    op.drop_column("users", "github_verified_at")
    op.drop_column("users", "github_html_url")
    op.drop_column("users", "github_avatar_url")
    op.drop_column("users", "github_id")
