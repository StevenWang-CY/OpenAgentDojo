"""Add ``submissions.manifest_sha256`` so graded runs anchor to a manifest hash.

Revision ID: 0008_submission_manifest_sha
Revises: 0007_dim_max_score_rename
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_submission_manifest_sha"
down_revision: Union[str, None] = "0007_dim_max_score_rename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("submissions", "manifest_sha256")
