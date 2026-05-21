"""Public profile schemas — what `GET /profiles/{handle}` returns.

Mirrors ``PublicProfile`` in ``packages/shared-types/src/api.ts`` (M7 §13.1).
The endpoint is public; nothing here may leak PII (no email, no ip, no
``user.id``) — only the handle, display name, joined-at, badges, recent
mission history, and aggregate score stats.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.mission import Difficulty

# Mirrors ``RubricDimension`` in packages/shared-types/src/api.ts. The seven
# keys come from the plan §11.1 rubric and are the union of keys the grader
# may emit under ``score_report.dimensions``.
RubricDimension = str  # constrained values; OpenAPI surfaces all seven below

_RUBRIC_DIMENSIONS: tuple[str, ...] = (
    "final_correctness",
    "verification",
    "agent_review",
    "prompt_quality",
    "context_selection",
    "safety",
    "diff_minimality",
)


class BadgeRead(BaseModel):
    """One row from the ``badges`` catalog."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    icon: str


class EarnedBadgeRead(BadgeRead):
    """Badge plus when/where it was earned (from ``user_badges``)."""

    earned_at: datetime
    session_id: uuid.UUID | None = None


class MissionHistoryItemRead(BaseModel):
    """A single graded session entry on a user's profile."""

    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    mission_id: str
    mission_title: str
    completed_at: datetime | None = None
    score: int | None = None
    difficulty: Difficulty


class PublicProfile(BaseModel):
    """Payload of `GET /api/v1/profiles/{handle}`.

    Public — no auth required, never include PII.
    """

    model_config = ConfigDict(from_attributes=True)

    handle: str
    display_name: str | None = None
    joined_at: datetime
    badges: list[EarnedBadgeRead] = Field(default_factory=list)
    history: list[MissionHistoryItemRead] = Field(default_factory=list)
    # Only dimensions that appeared in at least one of the user's submissions
    # are populated; absent keys mean the dimension was never scored.
    radar_averages: dict[str, float] = Field(default_factory=dict)
    total_missions: int = 0
    best_score: int | None = None


__all__ = [
    "BadgeRead",
    "EarnedBadgeRead",
    "MissionHistoryItemRead",
    "PublicProfile",
]
