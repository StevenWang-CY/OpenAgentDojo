"""P4.1 audit — recommendation cache rebuild fidelity payload.

Adds ``user_recommendations.extras JSONB NULL`` so the cache rebuild
path can preserve the per-item alignment + freshness flags computed
at engine-call time. Without this column, the rebuild path silently
re-derives alignment against the user's *current* radar — which
produces non-deterministic "why" copy for the same row whenever the
weakest dim drifts between the miss and the next hit.

Payload shape (today)::

    {
      "items": [
        {"mission_id": "...", "alignment": 0.5},
        ...
      ]
    }

The column is intentionally nullable so old rows (written before
0027) still round-trip through the rebuild path — the cache.py
rebuild has a fall-back that recomputes alignment from the live
catalogue when ``extras`` is absent.

SQLite divergence
-----------------
SQLite is reached via ``Base.metadata.create_all`` in
``tests/conftest.py`` (not Alembic), so the migration is a no-op in
tests. The ORM column maps to ``JSONB`` on Postgres and is patched
to ``JSON`` for SQLite at table-creation time by the
``_patch_models_for_sqlite`` shim.

Revision ID: 0027_recommendation_cache_extras
Revises: 0026_recommendation_cache
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_recommendation_cache_extras"
down_revision: str | None = "0026_recommendation_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_recommendations",
        sa.Column(
            "extras",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("user_recommendations", "extras")
