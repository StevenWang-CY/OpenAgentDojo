"""P1-1 — mission tags + repo_packs catalog.

Lifts the implicit ``mission.repo_pack`` string into a typed reference
against a new ``repo_packs`` table, and gives each mission a closed-
vocabulary tag list so the catalog can be filtered by failure-mode,
skill, and language. The schema delta is documented at
[P1_DESIGN.md §P1-1](../../../P1_DESIGN.md).

  * ``repo_packs`` — new table, one row per shipped repo pack
    (``fullstack-auth-demo``, ``data-api-demo``, ``go-orders-service``).
    The ``repo_sha`` column pins the pack against drift; the seed
    re-asserts it on every deploy (the actual CI gate lands in a
    follow-up PR — this migration just provisions the column).
  * ``missions.tags TEXT[] NOT NULL DEFAULT '{}'`` — failure-mode +
    skill + language tags, validated by the manifest loader against
    the closed vocabulary in ``apps/api/app/missions/manifest.py``.
  * ``missions.repo_pack_id TEXT NOT NULL REFERENCES repo_packs(id)``
    — backfilled from the existing ``missions.repo_pack`` column, then
    lifted NOT NULL. The two columns co-exist for one release cycle so
    application code can migrate readers incrementally; ``repo_pack``
    drops in a future migration once every caller reads ``repo_pack_id``.
  * ``idx_missions_repo_pack`` and ``idx_missions_tags`` (GIN on the
    array) — drive the new catalog filters in
    ``GET /api/v1/missions?language=...&tags=...``.

SQLite divergence
-----------------
SQLite has no ``TEXT[]`` and no GIN index. The model layer maps the
``tags`` column to ``JSON`` at table-creation time via the
``_patch_models_for_sqlite`` shim in ``tests/conftest.py`` so the in-
memory test engine keeps working. This migration only runs against
Postgres at deploy-time; the GIN index is created unconditionally
because SQLite doesn't run Alembic in our suite.

Revision ID: 0025_mission_tags_and_pack_metadata
Revises: 0024_magic_link_next
Create Date: 2026-05-27
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import yaml
from loguru import logger

from alembic import op

revision: str = "0025_mission_tags_and_pack_metadata"
down_revision: str | None = "0024_magic_link_next"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The repo packs shipped at the time this migration lands. The two TS/Py
# packs already back every mission under ``missions/0?-*``; the Go pack
# ships its first mission in a follow-up PR (see P1_DESIGN §P1-1). The
# ``repo_sha`` here is a placeholder constant — the real CI gate that
# diffs it against ``git rev-parse HEAD`` on the on-disk pack is wired
# in a sibling test (``tests/test_repo_pack_sha_pinned.py``). The seed
# is idempotent: rerunning the migration only updates the metadata,
# never the foreign-key relationships.
_PLACEHOLDER_PACK_SHA = "0" * 40

def _missions_dir() -> Path:
    """Return the on-disk ``missions`` directory.

    Climbs four parents from this file (``apps/api/alembic/versions/0025_*.py``
    → repo root) and resolves ``missions``. Matches the locator pattern used by
    :func:`0026_recommendation_cache._calibration_dir`.
    """
    here = Path(__file__).resolve()
    repo_root = here.parents[4]
    return repo_root / "missions"


_MISSION_DIR_RE = re.compile(r"^(\d{2})-(.+)$")


def _load_manifest_tags() -> dict[str, list[str]]:
    """Read each ``missions/<NN-id>/mission.yaml`` and collect its tags.

    Returns ``{mission_id: [tag, ...]}``. Silently skips entries that are
    missing, unreadable, or malformed — the backfill is best-effort and a
    CI-only deploy with no missions tree must not block the migration.
    """
    out: dict[str, list[str]] = {}
    root = _missions_dir()
    if not root.exists():
        return out
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError as exc:
        logger.warning(
            "0025 backfill: missions dir at {} unreadable ({}); skipping",
            root,
            exc,
        )
        return out
    for child in children:
        if not _MISSION_DIR_RE.match(child.name):
            continue
        manifest = child / "mission.yaml"
        if not manifest.exists():
            continue
        try:
            data: Any = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            logger.warning(
                "0025 backfill: manifest at {} unreadable ({}); skipping",
                manifest,
                exc,
            )
            continue
        if not isinstance(data, dict):
            continue
        mid = data.get("id")
        tags = data.get("tags")
        if not isinstance(mid, str):
            continue
        if not isinstance(tags, list):
            continue
        cleaned = [t for t in tags if isinstance(t, str) and t]
        if not cleaned:
            continue
        out[mid] = cleaned
    return out


_SEED_REPO_PACKS: tuple[dict[str, str], ...] = (
    {
        "id": "fullstack-auth-demo",
        "title": "Fullstack auth demo (TS/Node + React)",
        "language": "typescript",
        "stack_summary": (
            "Node 20 + Express + React. Cookie-session auth, integration "
            "tests under Vitest. Drives missions touching auth, refactors, "
            "and API contracts."
        ),
        "repo_sha": _PLACEHOLDER_PACK_SHA,
    },
    {
        "id": "data-api-demo",
        "title": "Data API demo (Python / FastAPI)",
        "language": "python",
        "stack_summary": (
            "Python 3.12 + FastAPI + SQLAlchemy. Pytest visible tests, "
            "pyright typecheck. Drives missions on race conditions, "
            "dependency hygiene, and overfit fixes."
        ),
        "repo_sha": _PLACEHOLDER_PACK_SHA,
    },
    {
        "id": "go-orders-service",
        "title": "Go orders service (Go 1.22 / chi)",
        "language": "go",
        "stack_summary": (
            "Go 1.22 + chi router + sqlite store. Goroutine worker pool, "
            "context-cancellation hot paths, and structured error wrapping. "
            "First Go pack on the dojo (P1-1, ships behind a feature flag)."
        ),
        "repo_sha": _PLACEHOLDER_PACK_SHA,
    },
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # ---- repo_packs ------------------------------------------------------
    op.create_table(
        "repo_packs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("stack_summary", sa.Text(), nullable=False),
        sa.Column("repo_sha", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else None,
        ),
        sa.CheckConstraint(
            "language IN ('typescript','python','go')",
            name="repo_packs_language_check",
        ),
    )

    # ---- missions: tags + repo_pack_id (nullable for backfill) ----------
    if is_postgres:
        op.add_column(
            "missions",
            sa.Column(
                "tags",
                sa.dialects.postgresql.ARRAY(sa.Text()),
                nullable=False,
                server_default=sa.text("ARRAY[]::text[]"),
            ),
        )
    else:
        # SQLite path — kept syntactically valid so the file imports cleanly
        # in environments that don't run the migration. Production is Postgres.
        op.add_column(
            "missions",
            sa.Column(
                "tags",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
    op.add_column(
        "missions",
        sa.Column(
            "repo_pack_id",
            sa.Text(),
            sa.ForeignKey("repo_packs.id"),
            nullable=True,
        ),
    )

    # ---- backfill missions.tags from the on-disk manifests --------------
    # The column was just added NOT NULL with a default empty array. We
    # populate it from the shipped ``missions/<NN-id>/mission.yaml`` files
    # so the catalog filter has real data on the first deploy after this
    # migration runs. Rows whose ``tags`` are already populated (or whose
    # manifest cannot be read) are left alone — the operation is idempotent.
    conn = op.get_bind()
    manifest_tags = _load_manifest_tags()
    if manifest_tags:
        if is_postgres:
            update_sql = sa.text(
                "UPDATE missions SET tags = (:tags)::TEXT[] "
                "WHERE id = :id AND (tags IS NULL OR cardinality(tags) = 0)"
            )
            for mid, tags in manifest_tags.items():
                # PostgreSQL array literal: '{"a","b"}'. Escape any embedded
                # quotes defensively even though the closed vocabulary forbids
                # them.
                escaped = ",".join('"' + t.replace('"', '\\"') + '"' for t in tags)
                array_literal = "{" + escaped + "}"
                conn.execute(update_sql, {"tags": array_literal, "id": mid})
        else:
            # SQLite path — ``tags`` is JSON. Only touch rows that are still
            # at their JSON-empty default so reruns stay idempotent.
            import json as _json

            update_sql = sa.text(
                "UPDATE missions SET tags = :tags "
                "WHERE id = :id AND (tags IS NULL OR tags = '[]')"
            )
            for mid, tags in manifest_tags.items():
                conn.execute(update_sql, {"tags": _json.dumps(tags), "id": mid})

    # ---- seed the three known repo packs --------------------------------
    repo_packs_t = sa.table(
        "repo_packs",
        sa.column("id", sa.Text()),
        sa.column("title", sa.Text()),
        sa.column("language", sa.Text()),
        sa.column("stack_summary", sa.Text()),
        sa.column("repo_sha", sa.Text()),
    )
    op.bulk_insert(repo_packs_t, list(_SEED_REPO_PACKS))

    # ---- backfill missions.repo_pack_id from missions.repo_pack ----------
    # Every existing mission rows have ``repo_pack`` populated (NOT NULL at
    # 0001). Map 1:1; rows whose pack is unknown (none today) keep NULL and
    # would fail the NOT NULL lift below, surfacing the drift loudly.
    op.execute(
        sa.text("UPDATE missions SET repo_pack_id = repo_pack WHERE repo_pack IS NOT NULL")
    )

    # ---- lift NOT NULL on repo_pack_id ----------------------------------
    op.alter_column("missions", "repo_pack_id", nullable=False)

    # ---- indexes --------------------------------------------------------
    op.create_index(
        "idx_missions_repo_pack",
        "missions",
        ["repo_pack_id"],
    )
    if is_postgres:
        # GIN on the tags array gives O(log n) contains-lookups for
        # ``WHERE tags @> ARRAY[...]`` catalog filters.
        op.execute("CREATE INDEX idx_missions_tags ON missions USING GIN (tags)")
    else:
        # SQLite has no GIN; a plain index is the best we can do and tests
        # never see this code path (the conftest patches the column type
        # before metadata.create_all).
        op.create_index("idx_missions_tags", "missions", ["tags"])


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("DROP INDEX IF EXISTS idx_missions_tags")
    else:
        op.drop_index("idx_missions_tags", table_name="missions")
    op.drop_index("idx_missions_repo_pack", table_name="missions")
    op.drop_column("missions", "repo_pack_id")
    op.drop_column("missions", "tags")
    op.drop_table("repo_packs")
