"""Submission read schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SubmissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    final_diff: str = ""
    total_score: int
    # Lists per the shared-types contract (each entry is a TestRunResult
    # or ValidatorResult dict). Legacy rows persisted as ``dict[suite_name,
    # TestRunResult]`` are still accepted via the ``list | dict`` union so a
    # mid-deploy read of an older row doesn't 500. The unionised pydantic
    # validator returns the on-disk shape as-is.
    visible_test_results: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    hidden_test_results: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    validator_results: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    score_report: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Injected at read-time by GET /reports/{id}; not persisted to the DB.
    ideal_solution: str | None = None
    # P0-2 — the post-mortem walkthrough loads three diffs in the report:
    #
    #   * ``final_diff``         — the user's submitted diff (above).
    #   * ``ideal_solution_diff`` — the canonical fix, read from disk at
    #     report-render time. Gated on ``session.status == 'graded'`` so a
    #     mid-pipeline crash doesn't leak the answer.
    #   * ``agent_patch_diff``   — the agent's original (deliberately-flawed)
    #     patch. Same gating.
    ideal_solution_diff: str | None = None
    agent_patch_diff: str | None = None
    # P0-2 — deterministic list computed by
    # ``app.grading.diagnostics.compute_critical_moments`` and persisted to
    # its own JSONB column (migration 0012). Empty list when none of the
    # heuristics tripped.
    critical_moments: list[dict[str, Any]] = Field(default_factory=list)
    # P0-3 / P0-4 — when set, a post-grading rule capped the total. The
    # only legal value today is ``'gave_up'`` (the give-up affordance caps
    # at 50/100). The FE renders a chip in the report header when this is
    # non-null; the profile aggregator excludes capped attempts from
    # best-per-mission when an uncapped attempt exists. ``None`` means
    # "no cap applied".
    score_cap_reason: Literal["gave_up"] | None = None
    # P0-8 — proctored posture mirrored from ``session.mode`` at grade time.
    # The grading runner stamps ``True`` iff the producing session was
    # ``proctored``. Surfaced here so the FE can render the verified badge
    # on the report page and the owner-facing /sessions/{id}/submission
    # response carries the same honest signal the public verify envelope
    # does. Defaults to ``False`` so legacy rows + the failure-stub path
    # remain unambiguous self-study.
    verified: bool = False
    # P0-3 — injected at read-time by the reports endpoint (NOT persisted
    # on the submissions row; sourced from the join to ``sessions``). The
    # FE's Retry-mission CTA needs the mission id to call
    # ``createSession({mission_id, previous_session_id})`` without a second
    # roundtrip. ``None`` only when the join-side session row is missing
    # (an impossible state in production but defensively handled).
    mission_id: str | None = None
    # P0-11 — verification envelope hash + HMAC signature. Surfaced to
    # the owner so the FE can render the hash in the report footer
    # (matching what the public verify page shows). NULL only when the
    # row predates P0-11 and the backfill hasn't been re-run.
    verification_hash: str | None = None
    verification_signature: str | None = None


class VerifyEnvelopeRead(BaseModel):
    """Public response for ``GET /api/v1/verify/{submission_id}`` (P0-11).

    The shape mirrors the canonical envelope verbatim plus three
    surface-only fields the FE needs but that are NOT part of the hash:
    ``canonical_url`` (so the verify page knows its own URL),
    ``verification_hash`` (already computed at grade time), and
    ``verification_signature`` (HMAC of the hash). All three are
    persisted columns on ``submissions``; the page never re-derives them
    server-side because the hash MUST be a function of the row's
    grade-time state, never the current state of the joined rows.
    """

    schema_version: int
    submission_id: uuid.UUID
    handle: str
    display_name: str | None = None
    mission_id: str
    mission_title: str
    mission_version: int
    rubric_version: str
    total_score: int
    effective_max: int
    missed_failure_mode: bool
    score_cap_reason: Literal["gave_up"] | None = None
    proctored: bool
    attempt_index: int
    graded_at: str
    canonical_url: str
    verification_hash: str
    verification_signature: str


class ReportRenderRead(BaseModel):
    """Lifecycle row for the PDF / PNG render pipeline (P0-11).

    Returned by ``GET /api/v1/reports/{id}/render?kind=…`` when the
    render is still in flight, and by ``POST /api/v1/reports/{id}/render``
    when a force-render is enqueued. When ``status == 'ready'`` the
    route 302s to a signed URL instead of returning this body — the FE
    only sees this shape during the queued / running / failed lifecycle.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submission_id: uuid.UUID
    kind: Literal["pdf", "png"]
    status: Literal["queued", "running", "ready", "failed"]
    bytes: int | None = None
    error: str | None = None
    created_at: datetime
    ready_at: datetime | None = None
    poll_after_seconds: int | None = None
