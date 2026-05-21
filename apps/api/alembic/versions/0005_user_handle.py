"""Add users.handle (unique citext) for public profile URLs.

Revision ID: 0005_user_handle
Revises: 0004_add_submission_indexes
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_user_handle"
down_revision: Union[str, None] = "0004_add_submission_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("handle", postgresql.CITEXT(), nullable=True),
    )
    op.create_unique_constraint("users_handle_key", "users", ["handle"])


def downgrade() -> None:
    op.drop_constraint("users_handle_key", "users", type_="unique")
    op.drop_column("users", "handle")
