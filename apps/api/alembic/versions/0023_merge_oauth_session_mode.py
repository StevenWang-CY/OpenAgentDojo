"""Merge 0021 (github_oauth) and 0022 (session_mode) heads (Phase 4.A.1).

Both 0021 and 0022 declared ``down_revision = '0020_session_reset_event'`` so
alembic ends the chain with two heads. Without an explicit merge, ``alembic
upgrade head`` would refuse to choose between them and any future migration
would have to pick a parent (silently dropping the other branch's history).
This empty merge tells alembic the two branches reconvene here; subsequent
migrations declare this revision as their single parent.

Empty ``upgrade()`` / ``downgrade()`` — no schema work; this is purely a
graph-shape resolution.

Revision ID: 0023_merge_oauth_session_mode
Revises: 0021_github_oauth, 0022_session_mode
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0023_merge_oauth_session_mode"
down_revision: tuple[str, str] | str | None = (
    "0021_github_oauth",
    "0022_session_mode",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: this is a graph-shape merge of two parallel heads."""


def downgrade() -> None:
    """No-op: see :func:`upgrade`."""
