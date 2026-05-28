"""P1 §0.4 — LLM substrate cache table.

Provisions the chokepoint every LLM-augmented surface reads/writes
through (see [P1_DESIGN.md §0.4](../../../P1_DESIGN.md)):

  * ``llm_cache`` — one canonical generation per
    ``(domain, content_hash, prompt_version)`` tuple. The bytes here
    are what downstream signatures (P0-11 verify envelope, P1-6 replay
    artefact) hash against; the LLM is never invoked on the hot path.

The unique constraint is the cache invariant. Concurrent writers
collide on conflict and the cache.py layer re-selects rather than
overwriting — the first writer wins, every other writer reads back the
canonical row. That mirrors the prompt-judgement table discipline and
keeps the determinism contract intact even under traffic spikes.

Migration ordering
------------------
``down_revision = "0029_replay_artifact_index"``. That migration is
being authored in parallel by another agent; this migration only
depends on the linear chain ending in 0029. If 0029 is not yet present
when this file lands, alembic's standard ordering machinery is happy
once both files are on disk.

SQLite divergence
-----------------
SQLite is reached via ``Base.metadata.create_all`` in
``tests/conftest.py`` (not Alembic), so this migration is a no-op
under tests. The ORM model in ``apps/api/app/models/llm_cache.py``
defines the same shape; ``_patch_models_for_sqlite`` strips the
``gen_random_uuid`` server default and replaces it with a Python
``uuid.uuid4`` factory.

Revision ID: 0030_llm_cache
Revises: 0029_replay_artifact_index
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_llm_cache"
down_revision: str | None = "0029_replay_artifact_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "domain",
            "content_hash",
            "prompt_version",
            name="uq_llm_cache_lookup",
        ),
    )
    # The covering index lives alongside the unique constraint so the
    # cache lookup hits an index-only scan on the canonical key tuple.
    op.create_index(
        "idx_llm_cache_lookup",
        "llm_cache",
        ["domain", "content_hash", "prompt_version"],
    )


def downgrade() -> None:
    op.drop_index("idx_llm_cache_lookup", table_name="llm_cache")
    op.drop_table("llm_cache")
