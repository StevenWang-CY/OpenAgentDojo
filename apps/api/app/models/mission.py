"""Mission catalog row — content lives under /missions/<id>/."""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text
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
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    repo_pack: Mapped[str] = mapped_column(Text, nullable=False)
    initial_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_mode: Mapped[str] = mapped_column(Text, nullable=False)
    skills_tested: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
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
