"""P0-3 — per-mission attempt summary for the signed-in caller.

Surfaces ``your_attempts`` on ``GET /missions/{id}`` so the FE can render
the "// your attempts" strip on the mission detail page without a second
roundtrip. The shape (count, best, latest, delta) implements the public-
private split documented in ADR 0009:

  * **Public profile** uses best-per-mission (excludes give-ups when an
    uncapped attempt exists). Attempt count is never public.
  * **Private surface** (this function) shows count, best, latest, and
    the delta from first → latest so the user can see their own
    trajectory.

The selection policy mirrors ``app.profiles.router._best_per_mission``
exactly so the two surfaces never disagree about what "best" means for a
single mission. A tiny dedupe is kept here rather than imported from the
profiles module to avoid a circular dependency (profiles depends on
mission data; missions doesn't depend on profile internals).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import SessionRow
from app.models.submission import Submission
from app.schemas.mission import YourAttempts


async def load_your_attempts(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    mission_id: str,
) -> YourAttempts:
    """Return a ``YourAttempts`` summary for one (user, mission) pair.

    Returns a zero-valued ``YourAttempts(count=0, ...)`` when the user has
    never attempted this mission. The caller distinguishes "never attempted"
    from "anonymous" by the wrapping ``Optional`` at the response layer —
    this function always returns a populated object so the FE has a stable
    shape to render against.

    Grader-failure stubs are excluded from the count + best/latest
    selection (they're not real attempts).
    """
    stmt = (
        select(
            Submission.id.label("submission_id"),
            Submission.score_report,
            Submission.score_cap_reason,
            Submission.created_at,
            SessionRow.score,
            SessionRow.completed_at,
            SessionRow.attempt_index,
        )
        .join(SessionRow, SessionRow.id == Submission.session_id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.mission_id == mission_id,
            SessionRow.status == "graded",
        )
        # Ordered ASC so we can grab the FIRST attempt for the delta
        # computation in a single pass. ``completed_at`` may be null on
        # some legacy rows; fall back to ``created_at`` on the submission
        # which is always populated.
        .order_by(
            SessionRow.completed_at.asc().nullslast(),
            Submission.created_at.asc(),
        )
    )
    rows = (await db.execute(stmt)).all()

    real_attempts: list[Any] = []
    for row in rows:
        report = row.score_report
        if isinstance(report, dict) and report.get("is_stub"):
            continue
        real_attempts.append(row)

    if not real_attempts:
        return YourAttempts()

    # Latest = the row with the most recent ``completed_at`` (or
    # ``created_at`` as a fallback for legacy rows). Because the query
    # already orders ASC, the last item is the latest.
    latest = real_attempts[-1]
    first = real_attempts[0]

    # Best — same tier policy as ``app.profiles.router._best_per_mission``:
    # uncapped attempts always beat gave-up attempts; within tier, higher
    # score wins; ties → most recent. The frontend renders an indicator
    # when ``best_was_gave_up`` so the user understands the chip.
    best = real_attempts[0]
    for candidate in real_attempts[1:]:
        if _candidate_beats(candidate, best):
            best = candidate

    delta: int | None = None
    if len(real_attempts) >= 2 and isinstance(latest.score, int) and isinstance(
        first.score, int
    ):
        delta = int(latest.score) - int(first.score)

    return YourAttempts(
        count=len(real_attempts),
        best_score=int(best.score) if isinstance(best.score, int) else None,
        best_submission_id=best.submission_id,
        latest_score=int(latest.score) if isinstance(latest.score, int) else None,
        latest_submission_id=latest.submission_id,
        delta=delta,
        best_was_gave_up=best.score_cap_reason == "gave_up",
    )


def _candidate_beats(candidate: Any, current: Any) -> bool:
    """Tier-aware comparison mirroring profiles.router._candidate_beats."""
    candidate_capped = candidate.score_cap_reason is not None
    current_capped = current.score_cap_reason is not None
    if current_capped and not candidate_capped:
        return True
    if not current_capped and candidate_capped:
        return False
    cand_score = candidate.score if isinstance(candidate.score, int) else -1
    curr_score = current.score if isinstance(current.score, int) else -1
    if cand_score != curr_score:
        return cand_score > curr_score
    cand_t = candidate.completed_at
    curr_t = current.completed_at
    if cand_t is None and curr_t is None:
        return False
    if cand_t is None:
        return False
    if curr_t is None:
        return True
    return cand_t > curr_t


__all__ = ["load_your_attempts"]
