"""Seed the MVP badge catalog (plan §11.4).

Revision ID: 0002_seed_badges
Revises: 0001_initial
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_seed_badges"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BADGES: list[dict[str, str]] = [
    {
        "id": "regression-test-writer",
        "title": "Regression Test Writer",
        "description": "Added a regression test matching the mission's failure-mode keywords.",
        "icon": "test-tube",
    },
    {
        "id": "security-aware-reviewer",
        "title": "Security-Aware Reviewer",
        "description": "Caught a forbidden change and corrected it before submit.",
        "icon": "shield-check",
    },
    {
        "id": "agent-skeptic",
        "title": "Agent Skeptic",
        "description": "Issued a corrective prompt, opened the diff, and edited the agent's lines.",
        "icon": "search",
    },
    {
        "id": "minimal-diff",
        "title": "Minimal Diff",
        "description": "Hit the highest minimality score with strong final correctness.",
        "icon": "scissors",
    },
    {
        "id": "concurrency-debugger",
        "title": "Concurrency Debugger",
        "description": "Mission 08 cleared with all hidden race tests passing.",
        "icon": "git-merge",
    },
    {
        "id": "api-contract-guardian",
        "title": "API Contract Guardian",
        "description": "Mission 09 cleared with no regression across components.",
        "icon": "file-check",
    },
]


def upgrade() -> None:
    badges = sa.table(
        "badges",
        sa.column("id", sa.Text),
        sa.column("title", sa.Text),
        sa.column("description", sa.Text),
        sa.column("icon", sa.Text),
    )
    op.bulk_insert(badges, _BADGES)


def downgrade() -> None:
    ids = ", ".join(f"'{b['id']}'" for b in _BADGES)
    op.execute(f"DELETE FROM badges WHERE id IN ({ids})")
