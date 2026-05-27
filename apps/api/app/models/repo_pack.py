"""Repo pack catalog — one row per shipped base repository (P1-1).

Lifts the implicit ``mission.repo_pack`` string column into a typed
catalog so the public ``GET /api/v1/missions`` endpoint can filter by
language, surface a per-pack ``stack_summary`` in the catalog, and pin
each pack against drift through ``repo_sha`` (the CI gate that compares
this column against ``git rev-parse HEAD`` for the on-disk pack lands
in ``tests/test_repo_pack_sha_pinned.py``).

The three shipped packs at the time this model lands are
``fullstack-auth-demo`` (TS/Node), ``data-api-demo`` (Python), and
``go-orders-service`` (Go). The first Go mission ships behind a
feature flag in a sibling PR — see P1_DESIGN.md §P1-1.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at


class RepoPack(Base):
    __tablename__ = "repo_packs"
    __table_args__ = (
        CheckConstraint(
            "language IN ('typescript','python','go')",
            name="repo_packs_language_check",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # ``language`` is the chip rendered on the catalog filter strip; the
    # closed vocabulary mirrors the mission-manifest ``lang:*`` tag set.
    language: Mapped[str] = mapped_column(Text, nullable=False)
    stack_summary: Mapped[str] = mapped_column(Text, nullable=False)
    # Pin against drift. The CI gate diffs this against the actual repo
    # ``git rev-parse HEAD`` for the on-disk pack and fails the deploy
    # on mismatch.
    repo_sha: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RepoPack {self.id} lang={self.language}>"
