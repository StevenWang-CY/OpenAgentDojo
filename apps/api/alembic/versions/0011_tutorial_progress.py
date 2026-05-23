"""Add tutorial progress columns + mission.kind discriminator (P0-1).

This migration is forward-only safe — every new column carries a server
default that backfills cleanly across every existing row.

  * ``users.tutorial_completed_at`` (TIMESTAMPTZ, NULL) — set when the user
    completes Mission 00. NULL means "first-time visitor"; the catalog uses
    this to render the // start here banner.
  * ``users.tutorial_replay_count`` (INTEGER, default 0) — incremented every
    time the user re-runs the tutorial. Purely audit / content-tuning
    telemetry; never shown publicly.
  * ``missions.kind`` (TEXT, default 'standard', CHECK IN ('standard',
    'tutorial')) — the grading short-circuit reads this to skip scoring for
    tutorial missions.

Revision ID: 0011_tutorial_progress
Revises: 0010_prompt_judgement_audit_cols
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_tutorial_progress"
down_revision: Union[str, None] = "0010_prompt_judgement_audit_cols"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _reseed_catalog_from_disk() -> None:
    """Re-scan the on-disk missions/ tree and upsert every row.

    The 0003 seed only sees the missions that existed when the migration
    was authored. Re-running the loader here lets P0-1's
    ``missions/00-orientation/`` (kind='tutorial') get picked up without
    having to maintain a parallel hand-written INSERT list.

    Failures here are surfaced loudly: a missing manifest, a malformed
    YAML, or a mission with bad ``kind`` value should fail the migration
    so the operator sees the drift immediately. The 0003 seed already
    sets this precedent.
    """
    from app.config import get_settings  # noqa: PLC0415
    from app.missions.loader import MissionLoader  # noqa: PLC0415

    settings = get_settings()
    loader = MissionLoader(settings.missions_root)
    loaded = loader.scan()
    if not loaded:
        return  # nothing to upsert; the 0003 seed already populated everything

    conn = op.get_bind()
    stmt = sa.text(
        """
        INSERT INTO missions
          (id, title, difficulty, category, repo_pack, initial_commit,
           estimated_minutes, failure_mode, skills_tested,
           manifest_sha256, version, published, kind)
        VALUES
          (:id, :title, :difficulty, :category, :repo_pack, :initial_commit,
           :estimated_minutes, :failure_mode, :skills_tested,
           :manifest_sha256, :version, :published, :kind)
        ON CONFLICT (id) DO UPDATE SET
           title             = EXCLUDED.title,
           difficulty        = EXCLUDED.difficulty,
           category          = EXCLUDED.category,
           repo_pack         = EXCLUDED.repo_pack,
           initial_commit    = EXCLUDED.initial_commit,
           estimated_minutes = EXCLUDED.estimated_minutes,
           failure_mode      = EXCLUDED.failure_mode,
           skills_tested     = EXCLUDED.skills_tested,
           manifest_sha256   = EXCLUDED.manifest_sha256,
           version           = EXCLUDED.version,
           published         = EXCLUDED.published,
           kind              = EXCLUDED.kind
        """
    )
    for m in loaded:
        conn.execute(stmt, m.to_catalog_row())


def upgrade() -> None:
    # users — tutorial progress columns.
    op.add_column(
        "users",
        sa.Column("tutorial_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "tutorial_replay_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # missions — kind discriminator. Default 'standard' so existing rows
    # backfill safely; the CHECK constraint pins the small enum so a bad
    # value can never sneak in via direct SQL.
    op.add_column(
        "missions",
        sa.Column(
            "kind",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'standard'"),
        ),
    )
    op.create_check_constraint(
        "missions_kind_check",
        "missions",
        "kind IN ('standard','tutorial')",
    )

    # Re-scan disk so the orientation tutorial gets picked up alongside
    # the seed (the 0003 migration shipped before P0-1 existed).
    _reseed_catalog_from_disk()


def downgrade() -> None:
    op.drop_constraint("missions_kind_check", "missions", type_="check")
    op.drop_column("missions", "kind")
    op.drop_column("users", "tutorial_replay_count")
    op.drop_column("users", "tutorial_completed_at")
