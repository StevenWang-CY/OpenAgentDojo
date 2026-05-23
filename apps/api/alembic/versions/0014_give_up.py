"""Add sessions.gave_up_at (P0-4 give-up affordance).

When a user invokes the give-up endpoint, the server stamps
``sessions.gave_up_at`` BEFORE running the submit pipeline. The grading
runner reads this flag at score-time and applies a 50/100 total cap
(``score_cap_reason='gave_up'`` on the resulting submission). Dimension
scores themselves are NOT mutated — the cap is applied at the report-total
level so the breakdown remains honest.

``score_cap_reason`` itself was added by migration 0013 (shared with the
multi-attempt scoring policy). If for any reason 0014 ships first against an
empty database, the column would still need to exist — but with the current
ordering (P0_DESIGN §0.1) 0013 lands first.

Forward-only safe (nullable column with no default).

Revision ID: 0014_give_up
Revises: 0013_multi_attempt
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_give_up"
down_revision: Union[str, None] = "0013_multi_attempt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("gave_up_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "gave_up_at")
