"""Add audit columns to ``prompt_judgements``.

After Phase-3 audit: the original 0009 table omits ``mission_revision`` and
``prior_agent_response_sha`` — the latter is now part of the cache key, so
forensic rows must persist it to be auditable. ``mission_revision`` (the
manifest content sha) is added so an analyst can see which mission
revision each cached judgement was created against.

Revision ID: 0010_prompt_judgement_audit_cols
Revises: 0009_prompt_judgements
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_prompt_judgement_audit_cols"
down_revision: Union[str, None] = "0009_prompt_judgements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# sha256("") — the digest for "no prior agent response" (turn 0 of a session).
_EMPTY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


def upgrade() -> None:
    op.add_column(
        "prompt_judgements",
        sa.Column(
            "mission_revision",
            sa.String(length=128),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "prompt_judgements",
        sa.Column(
            "prior_agent_response_sha",
            sa.String(length=64),
            nullable=False,
            server_default=_EMPTY_SHA256,
        ),
    )


def downgrade() -> None:
    op.drop_column("prompt_judgements", "prior_agent_response_sha")
    op.drop_column("prompt_judgements", "mission_revision")
