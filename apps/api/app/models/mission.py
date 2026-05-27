"""Mission catalog row — content lives under /missions/<id>/."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, event
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Mission(Base):
    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint(
            "difficulty IN ('beginner','intermediate','advanced')",
            name="missions_difficulty_check",
        ),
        CheckConstraint(
            "kind IN ('standard','tutorial')",
            name="missions_kind_check",
        ),
        # P1-2 — closed vocabulary mirror of RUBRIC_DIMENSIONS. The
        # corresponding migration 0026 lifts NOT NULL on a per-row basis
        # via the ``missions_kind_weak_dim_required`` CHECK below.
        CheckConstraint(
            "expected_weak_dim IS NULL OR expected_weak_dim IN ("
            "'final_correctness','verification','agent_review',"
            "'prompt_quality','context_selection','safety','diff_minimality')",
            name="missions_expected_weak_dim_vocabulary_check",
        ),
        CheckConstraint(
            "kind = 'tutorial' OR expected_weak_dim IS NOT NULL",
            name="missions_kind_weak_dim_required",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    repo_pack: Mapped[str] = mapped_column(Text, nullable=False)
    # P1-1 — typed reference to ``repo_packs.id``. Co-exists with the
    # untyped ``repo_pack`` string column for one release cycle so the
    # application code can migrate readers incrementally. The migration
    # 0025 backfills this from ``repo_pack`` and lifts NOT NULL.
    repo_pack_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("repo_packs.id"),
        nullable=False,
    )
    initial_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_mode: Mapped[str] = mapped_column(Text, nullable=False)
    skills_tested: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    # P1-1 — closed-vocabulary tag list (failure-mode + skill + language).
    # The manifest validator enforces the vocabulary and the cap of 8 tags;
    # the catalog endpoint exposes ``?tags=...`` as a filter backed by the
    # GIN index in migration 0025.
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    # P1-2 — the single rubric dimension this mission is primarily designed
    # to exercise. NULL only for tutorial missions; the
    # ``missions_kind_weak_dim_required`` CHECK (migration 0026) enforces
    # standard missions populate this. The recommendation engine reads
    # this column to align user weakness → mission alignment.
    expected_weak_dim: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # P0-1 — ``tutorial`` short-circuits the grading runner and is excluded
    # from public catalog listings + skill aggregates (the catalog renders
    # tutorial missions in a dedicated "Start here" surface, not the grid).
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="standard", server_default="standard"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Mission {self.id} kind={self.kind}>"


@event.listens_for(Mission, "before_insert")
def _mirror_repo_pack_id_default(_mapper: Any, _connection: Any, target: Mission) -> None:
    """Default ``repo_pack_id`` from ``repo_pack`` when the caller omitted it.

    The loader populates both columns side-by-side at upsert time; but during
    the P1-1 → P1-1.5 migration window, several call sites (incl. existing
    tests that pre-date this column) construct ``Mission`` without setting
    ``repo_pack_id``. Mirroring the untyped ``repo_pack`` string here keeps
    those call sites working without forcing every test to pass both names
    (the migration 0025 backfill applies the same identity rule).
    """
    if getattr(target, "repo_pack_id", None) is None:
        pack = getattr(target, "repo_pack", None)
        if pack is not None:
            target.repo_pack_id = pack


# P4.1 audit: the ``before_insert`` listener that defaulted
# ``expected_weak_dim='safety'`` for standard missions was removed.
# Defaulting a load-bearing rubric dimension at insert time hid loader /
# fixture bugs (a manifest forgetting to declare ``expected_weak_dim``
# would silently persist as 'safety', distorting the recommendation
# engine's alignment scoring downstream). Call sites that construct
# ``Mission`` directly — including tests — now MUST set the column
# explicitly. The ``missions_kind_weak_dim_required`` CHECK still
# enforces the invariant at the DB level.
