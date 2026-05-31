"""P1-2 — adaptive next-mission recommendation cache + ``expected_weak_dim``.

Provisions the deterministic-ranking layer of the recommendation engine
([P1_DESIGN.md §P1-2](../../../P1_DESIGN.md)):

  * ``user_recommendations`` — materialised per-user cache of
    ``(weakest_dim, recommended_ids[3])``. Hot path hits the cache;
    misses recompute via ``app.recommendations.engine.recommend``.
    ``invalidated_at`` is stamped by
    :func:`app.recommendations.cache.invalidate_for_user` on every
    newly-graded submission.
  * ``missions.expected_weak_dim`` — the single rubric dimension each
    mission is designed to exercise. Backfilled at migration time from
    ``missions/_calibration/<id>.yaml`` using the spec algorithm: the
    argmin of ``(ideal.dimensions[d] / DIMENSION_MAX[d])`` with the
    canonical RUBRIC_DIMENSIONS order as tie-break.
  * Two CHECK constraints — closed-vocabulary on the value, and a
    ``kind = 'tutorial' OR expected_weak_dim IS NOT NULL`` invariant so
    a half-edited manifest can't ship a NULL column for a standard
    mission.

SQLite divergence
-----------------
SQLite is reached via ``Base.metadata.create_all`` in
``tests/conftest.py`` (not Alembic), so the migration is a no-op in
tests. The ORM CHECK constraints in
``apps/api/app/models/mission.py::__table_args__`` enforce the same
invariant at table-creation time on SQLite.

Revision ID: 0026_recommendation_cache
Revises: 0025_mission_tags_pack_metadata
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import yaml

from alembic import op

revision: str = "0026_recommendation_cache"
down_revision: str | None = "0025_mission_tags_pack_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Canonical RUBRIC_DIMENSIONS — duplicated here (instead of imported)
# so the migration runs cleanly against historical checkouts that lack
# the runtime grading module. Kept in lockstep with
# ``apps/api/app/grading/dimensions.py::RUBRIC_DIMENSIONS``.
_DIMENSION_MAX: dict[str, int] = {
    "final_correctness": 30,
    "verification": 15,
    "agent_review": 15,
    "prompt_quality": 10,
    "context_selection": 10,
    "safety": 10,
    "diff_minimality": 10,
}
_RUBRIC_ORDER: tuple[str, ...] = tuple(_DIMENSION_MAX.keys())


def _calibration_dir() -> Path:
    """Return the ``missions/_calibration`` directory.

    Prefers the runtime-configured ``settings.missions_root`` so a
    containerised deployment that ships missions under a non-standard
    path (e.g. an OCI volume mount) backfills correctly. Falls back to
    the ``__file__``-relative climb (``apps/api/alembic/versions/0026_*.py``
    → repo root → ``missions/_calibration``) when the settings import
    fails — that's the historical-checkout / Alembic-only-no-app path.
    The settings import is wrapped in a broad ``except`` because the
    config module pulls Pydantic + env-file resolution, neither of
    which we want to hard-require at migration time.
    """
    try:
        from app.config import get_settings

        settings = get_settings()
        path = settings.missions_root.parent / "missions" / "_calibration"
        if path.exists():
            return path
    except Exception:
        pass
    here = Path(__file__).resolve()
    # apps/api/alembic/versions/0026_*.py  → climb four parents to the repo root.
    return here.parents[4] / "missions" / "_calibration"


def _argmin_weakest_dim(dims: dict[str, Any]) -> str | None:
    """Return the dimension whose score-to-max ratio is lowest.

    Ties are broken by the canonical RUBRIC_DIMENSIONS order so the
    backfill is byte-deterministic across replays. Returns ``None`` when
    no rubric dimension carries a numeric score (an empty / malformed
    envelope shouldn't crash the migration).
    """
    ratios: list[tuple[str, float, int]] = []
    for idx, dim in enumerate(_RUBRIC_ORDER):
        raw = dims.get(dim)
        if not isinstance(raw, (int, float)):
            continue
        max_s = _DIMENSION_MAX[dim]
        ratios.append((dim, float(raw) / max_s, idx))
    if not ratios:
        return None
    # Sort by (ratio, canonical_index) — argmin with canonical tie-break.
    ratios.sort(key=lambda t: (t[1], t[2]))
    return ratios[0][0]


def _compute_weak_dim_for(mission_id: str) -> str | None:
    """Read the per-mission calibration YAML and return its weak dim.

    Looks for the ``scenarios -> ideal`` entry first; falls back to the
    scenario with the highest ``expected_total``. Returns ``None`` when
    no calibration file exists (the caller skips the row).
    """
    path = _calibration_dir() / f"{mission_id}.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return None
    scenarios = data.get("scenarios") or []
    if not isinstance(scenarios, list) or not scenarios:
        return None
    ideal = next(
        (s for s in scenarios if isinstance(s, dict) and s.get("name") == "ideal"),
        None,
    )
    if ideal is None:
        ideal = max(
            (s for s in scenarios if isinstance(s, dict)),
            key=lambda s: s.get("expected_total", 0),
            default=None,
        )
    if not isinstance(ideal, dict):
        return None
    dims = ideal.get("dimensions") or {}
    if not isinstance(dims, dict):
        return None
    return _argmin_weakest_dim(dims)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. user_recommendations cache table.
    # ------------------------------------------------------------------
    op.create_table(
        "user_recommendations",
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("weakest_dim", sa.Text(), nullable=True),
        sa.Column(
            "recommended_ids",
            sa.dialects.postgresql.ARRAY(sa.Text()),
            nullable=False,
            # An empty array is the natural "no recommendations yet" state.
            # Without a server_default a bare INSERT (raw-SQL backfill, an
            # accidental cache-warm row from a future migration) would trip
            # the NOT NULL constraint; the default sidesteps that footgun.
            server_default=sa.text("'{}'::TEXT[]"),
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # 2. missions.expected_weak_dim column + closed-vocabulary CHECK.
    # ------------------------------------------------------------------
    op.add_column(
        "missions",
        sa.Column("expected_weak_dim", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "missions_expected_weak_dim_vocabulary_check",
        "missions",
        "expected_weak_dim IS NULL OR expected_weak_dim IN ("
        "'final_correctness','verification','agent_review',"
        "'prompt_quality','context_selection','safety','diff_minimality')",
    )

    # ------------------------------------------------------------------
    # 3. Backfill from the calibration envelopes.
    # ------------------------------------------------------------------
    conn = op.get_bind()
    mission_rows = conn.execute(
        sa.text("SELECT id, kind FROM missions")
    ).all()
    update_sql = sa.text(
        "UPDATE missions SET expected_weak_dim = :dim WHERE id = :id"
    )
    for row in mission_rows:
        mid = str(row.id)
        kind = str(row.kind)
        if kind == "tutorial":
            # Tutorials don't appear in the recommendation ladder; the
            # CHECK constraint below tolerates a NULL on them.
            continue
        dim = _compute_weak_dim_for(mid)
        if dim is None:
            # No calibration file → leave NULL; the standard-mission
            # CHECK below would fail unless the caller fixes the
            # calibration. We do NOT silently default to a sentinel.
            continue
        conn.execute(update_sql, {"dim": dim, "id": mid})

    # ------------------------------------------------------------------
    # 4. Standard-mission requirement CHECK (runs AFTER backfill so the
    #    constraint validation succeeds against the populated rows).
    # ------------------------------------------------------------------
    op.create_check_constraint(
        "missions_kind_weak_dim_required",
        "missions",
        "kind = 'tutorial' OR expected_weak_dim IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "missions_kind_weak_dim_required",
        "missions",
        type_="check",
    )
    op.drop_constraint(
        "missions_expected_weak_dim_vocabulary_check",
        "missions",
        type_="check",
    )
    op.drop_column("missions", "expected_weak_dim")
    op.drop_table("user_recommendations")
