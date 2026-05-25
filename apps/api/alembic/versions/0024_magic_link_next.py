"""Magic-link ``next_path`` column + report_renders ``force`` column.

Two small additions chained off the 0023 merge node:

* ``magic_link_tokens.next_path VARCHAR(200) NULLABLE`` — Phase 4.A.13.
  Holds the same-origin relative path the ``GET /auth/callback`` should
  redirect to after minting the session cookie. NULL means "use the
  default of ``/missions``". The route validates the value against the
  shared FE-route allowlist (``app.auth.github_oauth._validate_return_to``)
  on every read, so a stale path minted under an older allowlist still
  gets sanitised before the redirect.

* ``report_renders.force BOOLEAN NOT NULL DEFAULT FALSE`` — Phase 4.A.20.
  Distinguishes a user-initiated force-render (``POST /reports/{id}/render``)
  from a system-initiated first-render (``GET /reports/{id}/render`` on a
  missing row). The 24h daily cap filters on this column so a freshly-
  graded report's automatic first render doesn't burn any of the user's
  force-rerender budget.

Revision ID: 0024_magic_link_next
Revises: 0023_merge_oauth_and_session_mode
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_magic_link_next"
down_revision: str | None = "0023_merge_oauth_and_session_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "magic_link_tokens",
        sa.Column("next_path", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "report_renders",
        sa.Column(
            "force",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("report_renders", "force")
    op.drop_column("magic_link_tokens", "next_path")
