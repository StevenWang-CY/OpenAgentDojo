"""Add submissions.critical_moments (P0-2) and evidence shape.

The post-mortem walkthrough surfaces a deterministic list of "critical
moments" — supervision events after which the user committed to the wrong
path. We persist them in their own JSONB column rather than burying them
inside ``score_report`` because:

  * The grading runner emits them post-score, so a separate column keeps
    the score-report shape stable for replay determinism.
  * Reports filter / sort by the presence of a critical moment so the
    column lookup is cheaper than digging through the JSONB blob.

``strengths`` / ``weaknesses`` keep their existing position inside
``score_report`` (JSONB) — the FE handles the legacy ``list[str]`` shape
in addition to the evidence-bearing ``list[StrengthEntry]`` so we don't
need a migration step for those.

Revision ID: 0012_post_mortem_evidence
Revises: 0011_tutorial_progress
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012_post_mortem_evidence"
down_revision: Union[str, None] = "0011_tutorial_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column(
            "critical_moments",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("submissions", "critical_moments")
