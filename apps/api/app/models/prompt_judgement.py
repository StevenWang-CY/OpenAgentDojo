"""Cache table for LLM-judged prompt scores (P0-1).

Each row is the deterministic verdict of the prompt-quality judge for a
particular ``(prompt_text, mission_id, mission_revision, rubric_version)``
tuple, captured the first time grading saw that combination. The cache is
the source of truth for replays — once written, a row is never re-judged
even if the underlying LLM is upgraded. To force a rescore, bump
``app.grading.prompt_judge.RUBRIC_VERSION``: that changes the cache key,
so old rows no longer match and the next grading run re-judges.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at


class PromptJudgement(Base):
    __tablename__ = "prompt_judgements"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # SHA-256 hex of (prompt_text, mission_id, mission_revision, rubric_version).
    # Uniqueness is the cache invariant: same key → identical judgement.
    cache_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    mission_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Source of truth for which mission revision this judgement was made
    # against. Mirrors ``PromptJudgeContext.mission_revision`` (which is the
    # manifest content-hash, NOT the integer ``manifest.version`` — the
    # latter is too easy to forget to bump). Auditing rows by mission
    # revision lets ops detect stale judgements after a content edit.
    mission_revision: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="1"
    )
    # SHA-256 of the prior agent response the prompt was judged against.
    # Persisted for audit so a row can be traced back to the exact
    # conversational context; the cache key already hashes this value, so
    # this column is for human/SRE forensics, not retrieval.
    prior_agent_response_sha: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    rubric_version: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    specificity: Mapped[float] = mapped_column(Float, nullable=False)
    constraint_axis: Mapped[float] = mapped_column(Float, nullable=False)
    engagement: Mapped[float] = mapped_column(Float, nullable=False)
    verifiability: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = created_at()
