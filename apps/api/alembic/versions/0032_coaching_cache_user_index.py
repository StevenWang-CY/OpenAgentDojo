"""P1-4 — link table tying coaching cache rows to the producing user.

The scratchpad coaching feature stamps a row in
``coaching_cache_user_index`` every time a user produces a fresh
``llm_cache`` row in the ``scratchpad_coaching`` domain. The deletion
worker JOINs through this table to find every cache row a given user
produced (the cache row's ``content_hash`` is a SHA-256 of a payload
that includes the user's notes hash; without the link we'd have to
reconstruct every input payload to identify the rows to drop, which is
brittle and prone to drift).

Both FKs cascade on delete:

* deleting a user wipes their index rows;
* deleting an ``llm_cache`` row wipes the link entries pointing at it.

See [P1_DESIGN.md §P1-4](../../../P1_DESIGN.md) → "Privacy & data flow"
for the broader privacy posture.

Migration ordering
------------------
``down_revision = "0031_coaching_opt_out"`` — 0031 added the per-user
opt-out boolean; 0032 adds the cleanup link needed by the deletion
worker on top of that.

SQLite divergence
-----------------
Tests reach SQLite via ``Base.metadata.create_all`` in
``tests/conftest.py`` (not Alembic), so this migration is a no-op
under the test harness. The ORM model in
``apps/api/app/models/coaching_cache_user_index.py`` defines the same
shape; the ``now()`` server default is stripped by
``_patch_models_for_sqlite``.

Revision ID: 0032_coaching_cache_user_index
Revises: 0031_coaching_opt_out
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032_coaching_cache_user_index"
down_revision: str | None = "0031_coaching_opt_out"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coaching_cache_user_index",
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "llm_cache_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("llm_cache.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "llm_cache_id",
            name="pk_coaching_cache_user_index",
        ),
    )
    op.create_index(
        "idx_coaching_cache_user",
        "coaching_cache_user_index",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_coaching_cache_user", table_name="coaching_cache_user_index"
    )
    op.drop_table("coaching_cache_user_index")
