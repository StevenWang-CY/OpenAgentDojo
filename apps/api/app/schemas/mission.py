"""Public mission schemas — what the catalog endpoints return."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

Difficulty = Literal["beginner", "intermediate", "advanced"]
MissionCategory = Literal[
    "auth",
    "testing",
    "security",
    "frontend",
    "api",
    "database",
    "refactoring",
    "agent-safety",
    "review",
    "debugging",
    "tutorial",
]
MissionKind = Literal["standard", "tutorial"]


MissionLanguage = Literal["typescript", "python", "go"]
MissionStatus = Literal["shipped", "coming_soon"]


class MissionListItem(BaseModel):
    """Minimal mission card payload for `GET /missions`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    short_description: str = ""
    # P1-1 — coming-soon entries don't have a difficulty / category band yet
    # (the calibration envelope only exists once the mission is real), so
    # they ship with default labels. ``shipped`` rows always carry the real
    # value; the FE branches on ``status`` before rendering filter chips.
    difficulty: Difficulty = "beginner"
    category: MissionCategory = "debugging"
    estimated_minutes: int = 0
    failure_mode_id: str = Field(
        default="",
        validation_alias=AliasChoices("failure_mode_id", "failure_mode"),
    )
    skills_tested: list[str] = Field(default_factory=list)
    version: int = 1
    published: bool = True
    # P0-1 — ``tutorial`` is rendered through the orientation surface rather
    # than the catalog grid, so the FE filters this on the client. Surfacing
    # the field on the list payload (instead of forcing a per-mission detail
    # fetch) keeps the catalog render single-roundtrip.
    kind: MissionKind = "standard"
    # P1-1 — typed FK to ``repo_packs.id`` (None on coming_soon placeholders
    # whose pack hasn't shipped yet). ``language`` is derived from the
    # pack's ``repo_packs.language`` (or the roadmap entry's own
    # ``language`` field for placeholders).
    repo_pack_id: str | None = None
    language: MissionLanguage = "typescript"
    tags: list[str] = Field(default_factory=list)
    # P1-1 — ``shipped`` for catalog rows; ``coming_soon`` for roadmap
    # entries appended when the caller passes ``?include=upcoming``. The
    # FE renders coming-soon cards muted with the dated chip.
    status: MissionStatus = "shipped"
    # Only populated when ``status == 'coming_soon'``; ISO ``YYYY-MM-DD``.
    target_release_date: str | None = None


class YourAttempts(BaseModel):
    """P0-3 — the signed-in user's attempt history against a single mission.

    Surfaced inline on the mission detail page so the "// your attempts" strip
    can render without a second roundtrip. ``count`` is the total graded
    attempts (capped + uncapped) so the strip honours the multi-attempt
    policy: ``best_score`` reflects the user's best non-gave-up attempt
    (falling back to the best gave-up attempt when no uncapped attempt
    exists), ``latest_score`` is the most recently graded attempt regardless
    of cap, and ``delta`` is the signed difference between latest and first.

    Attempt count is NEVER surfaced on the public profile — see
    `docs/adr/0009-multi-attempt-policy.md`.
    """

    count: int = 0
    best_score: int | None = None
    best_submission_id: uuid.UUID | None = None
    latest_score: int | None = None
    latest_submission_id: uuid.UUID | None = None
    # Signed delta from the first attempt to the latest. ``None`` when
    # count < 2 (no improvement to measure yet).
    delta: int | None = None
    # P0-4 — true when the user's best attempt was a give-up. The FE renders
    # a muted "gave up" hint beside the score so the strip is honest about
    # the cap.
    best_was_gave_up: bool = False
    # P0-3 — per-attempt total scores in chronological order (first → latest).
    # Powers the sparkline tooltip on the "delta vs first" cell. Empty for
    # the never-attempted case; a list of one int for first-attempt-only.
    # Capped at the most recent 12 attempts so a power user with 100s of
    # attempts doesn't bloat the payload — the sparkline tooltip is for
    # trajectory at a glance, not exhaustive history.
    score_history: list[int] = Field(default_factory=list)


class MissionDetail(BaseModel):
    """`GET /missions/{id}` payload.

    Withholds ``ideal_solution`` and any hidden-test surface; brief is included
    because the workspace needs it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    short_description: str = ""
    difficulty: Difficulty
    category: MissionCategory
    estimated_minutes: int
    failure_mode_id: str = Field(validation_alias=AliasChoices("failure_mode_id", "failure_mode"))
    skills_tested: list[str] = Field(default_factory=list)
    repo_pack: str
    initial_commit: str
    manifest_sha256: str
    version: int = 1
    published: bool = True
    brief: str = ""
    # P1-1 — ``go122`` is the third sandbox runtime (Go 1.22 / chi) shipped
    # with the ``go-orders-service`` repo pack. Kept in lockstep with
    # ``app.missions.manifest.LanguageRuntime`` so the catalog detail
    # response is honest about what the workspace can actually boot.
    language_runtime: Literal["node20", "python312", "go122"] | None = None
    visible_tests: list[str] = Field(default_factory=list)
    expected_context_required: list[str] = Field(default_factory=list)
    expected_context_recommended: list[str] = Field(default_factory=list)
    expected_diff_lines_p50: int | None = None
    # P0-1 — see MissionListItem.kind.
    kind: MissionKind = "standard"
    # P1-1 — typed FK to ``repo_packs.id`` (None on coming-soon detail
    # responses whose pack hasn't shipped yet). ``language`` is derived
    # from the pack's ``repo_packs.language`` (or defaults to ``typescript``
    # when no pack metadata exists), mirroring the catalog list response.
    repo_pack_id: str | None = None
    language: MissionLanguage = "typescript"
    tags: list[str] = Field(default_factory=list)
    # P1-1 — ``shipped`` for catalog rows; ``coming_soon`` for roadmap
    # entries. The FE renders coming-soon detail pages muted with the
    # dated chip; ``status`` is the canonical discriminator (NOT
    # ``published``, which stays ``True`` so the row is publicly readable).
    status: MissionStatus = "shipped"
    # Only populated when ``status == 'coming_soon'``; ISO ``YYYY-MM-DD``.
    target_release_date: str | None = None
    # P0-3 — populated only for signed-in callers. ``None`` for anonymous
    # viewers so the FE renders the catalog "Start mission" CTA without a
    # private overlay. ``count == 0`` (with non-null wrapper) means the
    # caller is signed in but has never attempted the mission — the
    # strip stays hidden and the CTA reads "Start mission".
    your_attempts: YourAttempts | None = None
