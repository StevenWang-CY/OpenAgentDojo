"""P1-4 §"Coaching reflection" — per-user opt-out for scratchpad coaching.

Adds a single boolean column ``users.coaching_reflections_enabled`` that
gates the post-mortem coaching reflection feature. The reflection is the
only LLM surface that sees user-private text (the scratchpad body), so
the design (P1_DESIGN.md §P1-4 → "Privacy & data flow") requires a
server-side toggle so Wave 2B's coaching endpoint can refuse to forward
the text to Bedrock when the user has opted out.

Defaults to TRUE for every account, both new and backfilled — consistent
with the existing analytics-by-default-after-consent posture from P0-5
(the cookie banner accept flow is the upstream gate; the per-feature
toggle here is for users who want the analytics rollup but specifically
not the scratchpad → Bedrock flow).

Migration ordering
------------------
``down_revision = "0030_llm_cache"``. Migration 0030 (the LLM cache
table) is the most-recent landed migration as of authoring; 0028/0029
landed before it in the parallel-agent plan and 0031 is the safe next
slot. The column is independent of the cache table — it gates a request,
not a row — but the linear chain matters for ``alembic upgrade head``.

SQLite divergence
-----------------
Tests reach SQLite via ``Base.metadata.create_all`` in
``tests/conftest.py`` (not Alembic), so this migration is a no-op under
the test harness. The ORM column in ``apps/api/app/models/user.py``
defines the same shape with ``server_default="1"`` and ``default=True``
so a fresh test DB always sees the opt-in default; the SQLite path
strips the ``true`` server default per ``_patch_models_for_sqlite``
(which we extended to handle ``true``/``false`` literals).

Revision ID: 0031_coaching_opt_out
Revises: 0030_llm_cache
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031_coaching_opt_out"
down_revision: str | None = "0030_llm_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``server_default='true'`` so any backfilled row (the entire
    # ``users`` table at migration-apply time) lands with the opt-in
    # default. ``nullable=False`` is the design's invariant — the
    # coaching endpoint reads this column on every call and a NULL
    # would otherwise force a defensive defaulting branch.
    op.add_column(
        "users",
        sa.Column(
            "coaching_reflections_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "coaching_reflections_enabled")
