"""Create the ``prompt_judgements`` cache table (P0-1).

Stores the deterministic LLM-judge verdict for each scored prompt, keyed
by SHA-256 of ``(prompt_text, mission_id, mission_revision, rubric_version)``.
The grader writes on cache miss and reads on every subsequent grading run
of the same session — replays are byte-identical even though an LLM was
involved.

Revision ID: 0009_prompt_judgements
Revises: 0008_submission_manifest_sha
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0009_prompt_judgements"
down_revision: Union[str, None] = "0008_submission_manifest_sha"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_judgements",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.Text(), nullable=False),
        sa.Column("rubric_version", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("specificity", sa.Float(), nullable=False),
        sa.Column("constraint_axis", sa.Float(), nullable=False),
        sa.Column("engagement", sa.Float(), nullable=False),
        sa.Column("verifiability", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "prompt_judgements_cache_key_uq",
        "prompt_judgements",
        ["cache_key"],
    )
    op.create_index(
        "idx_prompt_judgements_mission",
        "prompt_judgements",
        ["mission_id"],
    )
    op.create_index(
        "idx_prompt_judgements_cache_key",
        "prompt_judgements",
        ["cache_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_prompt_judgements_cache_key", table_name="prompt_judgements"
    )
    op.drop_index(
        "idx_prompt_judgements_mission", table_name="prompt_judgements"
    )
    op.drop_constraint(
        "prompt_judgements_cache_key_uq",
        "prompt_judgements",
        type_="unique",
    )
    op.drop_table("prompt_judgements")
