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
    # P1-6 / Wave 2C — the producing ``submissions.id`` for this graded
    # session. Surfaced so the FE can render a per-row "Replay" affordance
    # (the replay artefact endpoint keys off submission, not session). The
    # field is nullable for defensive forward-compatibility: any session
    # row that surfaces in history without a joined submission renders
    # the row without a Replay button rather than wiring a broken click.
    submission_id: uuid.UUID | None = None
    mission_id: str
    mission_title: str
    completed_at: datetime | None = None
    score: int | None = None
    difficulty: Difficulty


class DimensionTrendPoint(BaseModel):
    """One ``(completed_at, score)`` point on a per-dimension sparkline."""

    completed_at: datetime
    score: int


class PublicProfile(BaseModel):
    """Payload of `GET /api/v1/profiles/{handle}`.

    Public — no auth required, never include PII.
    """

    model_config = ConfigDict(from_attributes=True)

    handle: str
    display_name: str | None = None
    joined_at: datetime
    # P0-7 — GitHub OAuth identity verification fields on the PUBLIC
    # profile. ``github_verified_at`` is the FE's authoritative signal for
    # the verified-via-GitHub chip; ``github_html_url`` is the link target
    # so consumers can independently sanity-check the identity. All four
    # are nullable: legacy / email-only profiles render the FE's
    # "self-attested" chip when ``github_verified_at`` is null.
    github_login: str | None = None
    github_avatar_url: str | None = None
    github_html_url: str | None = None
    github_verified_at: datetime | None = None
    badges: list[EarnedBadgeRead] = Field(default_factory=list)
    history: list[MissionHistoryItemRead] = Field(default_factory=list)
    # Only dimensions that appeared in at least one of the user's submissions
    # are populated; absent keys mean the dimension was never scored.
    # P0-8 — when ``verified_attempts_only`` is true (default whenever any
    # verified attempt exists), this map carries the verified bucket; when
    # false (no verified attempts on file) it carries every graded
    # attempt — honest fallback, paired with the
    # ``has_verified_attempts: false`` flag so the FE renders the
    # honor-mode notice.
    radar_averages: dict[str, float] = Field(default_factory=dict)
    # P0-8 — separate radar built from verified (proctored) attempts only.
    # Populated only when ``has_verified_attempts`` is true; the FE flips
    # the radar to this set when the viewer toggles "Verified only" off.
    # ``None`` is the explicit "no verified attempts to render" signal.
    dimension_history_verified: dict[str, float] | None = None
    # P0-8 — true iff at least one verified (proctored) submission exists
    # for this profile. Drives the FE's default toggle position (verified
    # only when this is true; show-all otherwise) and the honor-mode
    # notice.
    has_verified_attempts: bool = False
    # P0-8 — the partition policy actually applied to ``radar_averages``.
    # ``True`` means the radar excludes honor-mode attempts; ``False``
    # means it includes every graded attempt (the honest path when no
    # verified attempt exists on file).
    verified_attempts_only: bool = False
    # Per-dimension chronological score trail for the longitudinal sparklines
    # (P2-2). Each dimension maps to a list of ``(completed_at, score)``
    # points, oldest first. Pending dimension scores (``null``) are skipped
    # — a sparkline plots only points the grader could actually measure.
    dimension_trends: dict[str, list[DimensionTrendPoint]] = Field(default_factory=dict)
    total_missions: int = 0
    best_score: int | None = None


class FailureModeMastery(BaseModel):
    """Per-failure-mode mastery summary for the logged-in user (P2-3).

    Powers the "skills" / "failure-mode catalog" page: the user sees, for
    each of the 10 supervision failure modes, how many sessions they
    attempted, how many they passed (hidden tests green), and their
    average score across attempts.
    """

    failure_mode: str
    failure_mode_title: str | None = None
    mission_ids: list[str] = Field(default_factory=list)
    mission_titles: list[str] = Field(default_factory=list)
    sessions_attempted: int = 0
    sessions_passed: int = 0
    avg_score: float | None = None
    best_score: int | None = None
    last_attempted_at: datetime | None = None


class SkillsCatalog(BaseModel):
    """Payload of ``GET /api/v1/profiles/me/skills``."""

    failure_modes: list[FailureModeMastery] = Field(default_factory=list)
    total_missions: int = 0
    total_failure_modes: int = 0


__all__ = [
    "BadgeRead",
    "DimensionTrendPoint",
    "EarnedBadgeRead",
    "FailureModeMastery",
    "MissionHistoryItemRead",
    "PublicProfile",
    "SkillsCatalog",
]
