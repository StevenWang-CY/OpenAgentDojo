"""Materialised next-mission recommendation cache (P1-2).

One row per user; cleared by :func:`app.recommendations.cache.invalidate_for_user`
on three events documented in [P1_DESIGN.md §P1-2](../../../P1_DESIGN.md):

  * the user grades a new submission (most common path);
  * a new mission is published — handled by a one-shot script;
  * a rubric re-balance — handled by the same one-shot script.

The row stores the *deterministic ranking output only* (weakest dimension
+ the top-3 recommended mission ids). The diagnosis prose lives in the
LLM-cache layer (P1-2 PR2); the engine recomputes the deterministic
ranking on cache miss + a fresh-or-not signal is exposed to clients via
``RecommendationSet.cache_hit``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRecommendation(Base):
    """Per-user materialised recommendation row.

    ``recommended_ids`` is an ordered list (length 3 in steady state; the
    "everything graded" edge-case writes one shipped + two ``coming_soon``
    ids — the FE renders the coming-soon entries with their own surface).
    ``invalidated_at`` is set by :func:`invalidate_for_user`; readers
    treat any row with ``invalidated_at IS NOT NULL`` as a cache miss
    and recompute eagerly.
    """

    __tablename__ = "user_recommendations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    weakest_dim: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # P4.1 audit — cache rebuild fidelity payload (migration 0027).
    # Today this stores
    # ``{"items":[{"mission_id":"...", "alignment": 0.5}, ...]}`` so a
    # cache hit can re-render the original "why" copy without re-deriving
    # alignment against a drifted radar (the engine's alignment scoring
    # depends on the user's current weakest dim, which changes between
    # the miss and the next hit). Optional / nullable so older rows
    # written before 0027 still rehydrate via the fall-back path.
    extras: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<UserRecommendation user_id={self.user_id} "
            f"weakest_dim={self.weakest_dim} "
            f"ids={self.recommended_ids!r}>"
        )
