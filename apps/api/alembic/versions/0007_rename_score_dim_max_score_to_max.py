"""Rename ``score_report.dimensions[*].max_score`` to ``max``.

Phase 4 contract alignment: the grading engine now emits ``{"score", "max",
"signals"}`` per dimension (see ``apps/api/app/grading/score.py``). Historic
submissions persisted the older ``max_score`` key; the radar/report UIs read
``max`` directly so legacy rows would render the gauge denominator as
``undefined``.

This migration rewrites each row's ``score_report->'dimensions'`` JSONB so
that every nested dimension object swaps ``max_score`` for ``max`` while
preserving every other key (``score``, ``signals``, future additions). Rows
without a ``score_report``, without a ``dimensions`` object, or whose
dimensions never carried ``max_score`` are skipped — the SQL is idempotent
and can be re-run safely.

Revision ID: 0007_rename_score_dim_max_score_to_max
Revises: 0006_session_last_activity
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_rename_score_dim_max_score_to_max"
down_revision: Union[str, None] = "0006_session_last_activity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UPGRADE_SQL = """
UPDATE submissions
SET score_report = jsonb_set(
    score_report,
    '{dimensions}',
    (
        SELECT jsonb_object_agg(
            d.key,
            (d.value - 'max_score') || jsonb_build_object('max', d.value->'max_score')
        )
        FROM jsonb_each(score_report->'dimensions') AS d
    )
)
WHERE score_report IS NOT NULL
  AND score_report ? 'dimensions'
  AND jsonb_typeof(score_report->'dimensions') = 'object'
  AND EXISTS (
      SELECT 1 FROM jsonb_each(score_report->'dimensions') AS d
      WHERE jsonb_typeof(d.value) = 'object'
        AND d.value ? 'max_score'
  );
"""


_DOWNGRADE_SQL = """
UPDATE submissions
SET score_report = jsonb_set(
    score_report,
    '{dimensions}',
    (
        SELECT jsonb_object_agg(
            d.key,
            (d.value - 'max') || jsonb_build_object('max_score', d.value->'max')
        )
        FROM jsonb_each(score_report->'dimensions') AS d
    )
)
WHERE score_report IS NOT NULL
  AND score_report ? 'dimensions'
  AND jsonb_typeof(score_report->'dimensions') = 'object'
  AND EXISTS (
      SELECT 1 FROM jsonb_each(score_report->'dimensions') AS d
      WHERE jsonb_typeof(d.value) = 'object'
        AND d.value ? 'max'
  );
"""


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    # The rewrite uses Postgres-only JSONB helpers (``jsonb_each``,
    # ``jsonb_object_agg``, ``jsonb_set``). SQLite (used by the test
    # harness) has no equivalent; we no-op there so the test DB can still
    # stamp the migration without exploding.
    if not _is_postgres():
        return
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(_DOWNGRADE_SQL)
