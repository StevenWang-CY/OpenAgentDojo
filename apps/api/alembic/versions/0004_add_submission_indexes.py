"""Add indexes on submissions for faster lookups.

Revision ID: 0004_add_submission_indexes
Revises: 0003_seed_missions
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_add_submission_indexes"
down_revision: Union[str, None] = "0003_seed_missions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Index for looking up the submission for a given session (FK lookup).
    op.create_index(
        "idx_submissions_session",
        "submissions",
        ["session_id"],
    )
    # Index for chronological listing of all submissions (newest first).
    op.create_index(
        "idx_submissions_created",
        "submissions",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_submissions_created", table_name="submissions")
    op.drop_index("idx_submissions_session", table_name="submissions")
