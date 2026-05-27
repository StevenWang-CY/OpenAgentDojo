"""Seed the mission catalog.

Reads every ``mission.yaml`` under the configured ``MISSIONS_ROOT``,
validates each manifest against the Pydantic model, and upserts the
catalog row. **No silent-stub fallback** — if the loader fails or finds
zero manifests, the migration raises so we never ship an empty catalog.

The mission ids are duplicated in ``_MISSION_IDS`` purely so the
``downgrade`` path can DELETE every row this migration could have
written (alembic does not preserve runtime state across the
upgrade/downgrade boundary). Keep the list in sync with
``missions/NN-*/mission.yaml::id``.

Revision ID: 0003_seed_missions
Revises: 0002_seed_badges
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_seed_missions"
down_revision: Union[str, None] = "0002_seed_badges"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mission ids covered by this seed. Mirrors `id:` in every
# `missions/NN-*/mission.yaml`. Kept here so `downgrade()` can DELETE
# the same rows it inserted, even when the on-disk content has changed.
_MISSION_IDS: tuple[str, ...] = (
    "auth-cookie-expiration",
    "agent-wrong-file",
    "missing-regression-test",
    "overfitted-test-fix",
    "security-validation-removed",
    "excessive-rewrite",
    "dependency-misuse",
    "async-race-condition",
    "api-contract-drift",
    "typecheck-ignored",
    # P1-1 — Go-runtime missions tied to go-orders-service pack.
    "goroutine-leak",
    "context-cancel-dropped",
    "error-shadowed-by-wrap",
)


def _collect_missions_from_disk() -> list[dict[str, Any]]:
    """Scan the missions root via the runtime loader.

    Returns the catalog rows. Raises on any parse / validation error so
    the migration fails loudly when content drift breaks the schema.
    """
    from app.config import get_settings  # noqa: PLC0415
    from app.missions.loader import MissionLoader  # noqa: PLC0415

    settings = get_settings()
    loader = MissionLoader(settings.missions_root)
    loaded = loader.scan()
    if not loaded:
        raise RuntimeError(
            f"mission seed found zero manifests under {settings.missions_root}. "
            "Run from the repo root or set MISSIONS_ROOT to the missions/ "
            "directory before running migrations."
        )
    return [m.to_catalog_row() for m in loaded]


def upgrade() -> None:
    rows = _collect_missions_from_disk()

    # Use raw SQL with ON CONFLICT so reruns are idempotent. The P1-1
    # migration (0025) adds the ``repo_pack_id`` FK and ``tags`` array
    # columns; the P1-2 migration (0026) adds ``expected_weak_dim``.
    # We introspect the live schema once and pick the INSERT shape
    # matching the columns that exist. Idempotent reruns under 0026+
    # pick the fully-extended shape and update every newer column in
    # lockstep.
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    mission_cols = {c["name"] for c in inspector.get_columns("missions")}
    has_p1_columns = {"repo_pack_id", "tags"}.issubset(mission_cols)
    has_expected_weak_dim = "expected_weak_dim" in mission_cols

    if has_p1_columns and has_expected_weak_dim:
        stmt = sa.text(
            """
            INSERT INTO missions
              (id, title, difficulty, category, repo_pack, repo_pack_id,
               initial_commit, estimated_minutes, failure_mode,
               skills_tested, tags, expected_weak_dim,
               manifest_sha256, version, published)
            VALUES
              (:id, :title, :difficulty, :category, :repo_pack, :repo_pack_id,
               :initial_commit, :estimated_minutes, :failure_mode,
               :skills_tested, :tags, :expected_weak_dim,
               :manifest_sha256, :version, :published)
            ON CONFLICT (id) DO UPDATE SET
               title             = EXCLUDED.title,
               difficulty        = EXCLUDED.difficulty,
               category          = EXCLUDED.category,
               repo_pack         = EXCLUDED.repo_pack,
               repo_pack_id      = EXCLUDED.repo_pack_id,
               initial_commit    = EXCLUDED.initial_commit,
               estimated_minutes = EXCLUDED.estimated_minutes,
               failure_mode      = EXCLUDED.failure_mode,
               skills_tested     = EXCLUDED.skills_tested,
               tags              = EXCLUDED.tags,
               expected_weak_dim = EXCLUDED.expected_weak_dim,
               manifest_sha256   = EXCLUDED.manifest_sha256,
               version           = EXCLUDED.version,
               published         = EXCLUDED.published
            """
        )
        for row in rows:
            conn.execute(stmt, row)
    elif has_p1_columns:
        stmt = sa.text(
            """
            INSERT INTO missions
              (id, title, difficulty, category, repo_pack, repo_pack_id,
               initial_commit, estimated_minutes, failure_mode,
               skills_tested, tags, manifest_sha256, version, published)
            VALUES
              (:id, :title, :difficulty, :category, :repo_pack, :repo_pack_id,
               :initial_commit, :estimated_minutes, :failure_mode,
               :skills_tested, :tags, :manifest_sha256, :version, :published)
            ON CONFLICT (id) DO UPDATE SET
               title             = EXCLUDED.title,
               difficulty        = EXCLUDED.difficulty,
               category          = EXCLUDED.category,
               repo_pack         = EXCLUDED.repo_pack,
               repo_pack_id      = EXCLUDED.repo_pack_id,
               initial_commit    = EXCLUDED.initial_commit,
               estimated_minutes = EXCLUDED.estimated_minutes,
               failure_mode      = EXCLUDED.failure_mode,
               skills_tested     = EXCLUDED.skills_tested,
               tags              = EXCLUDED.tags,
               manifest_sha256   = EXCLUDED.manifest_sha256,
               version           = EXCLUDED.version,
               published         = EXCLUDED.published
            """
        )
        for row in rows:
            legacy_row = {
                k: v for k, v in row.items() if k != "expected_weak_dim"
            }
            conn.execute(stmt, legacy_row)
    else:
        stmt = sa.text(
            """
            INSERT INTO missions
              (id, title, difficulty, category, repo_pack, initial_commit,
               estimated_minutes, failure_mode, skills_tested,
               manifest_sha256, version, published)
            VALUES
              (:id, :title, :difficulty, :category, :repo_pack, :initial_commit,
               :estimated_minutes, :failure_mode, :skills_tested,
               :manifest_sha256, :version, :published)
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
               published         = EXCLUDED.published
            """
        )
        for row in rows:
            legacy_row = {
                k: v
                for k, v in row.items()
                if k not in {"repo_pack_id", "tags", "expected_weak_dim"}
            }
            conn.execute(stmt, legacy_row)


def downgrade() -> None:
    """Remove every mission row this seed could have written.

    We DELETE by id rather than ``TRUNCATE missions`` so user-added rows
    (if any) survive a rollback.
    """
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM missions WHERE id = ANY(:ids)"),
        {"ids": list(_MISSION_IDS)},
    )
