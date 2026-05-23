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

The selection policy is sourced from :mod:`app.grading.attempts` so the
mission-detail strip, the public radar, and the skills/mastery view can
never disagree about which attempt is "best".
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.grading.attempts import candidate_beats
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

    # Best — uses the shared tier policy in ``app.grading.attempts``:
    # uncapped attempts always beat gave-up attempts; within tier, higher
    # score wins; ties → most recent. The frontend renders an indicator
    # when ``best_was_gave_up`` so the user understands the chip.
    best = real_attempts[0]
    for candidate in real_attempts[1:]:
        if candidate_beats(candidate, best):
            best = candidate

    delta: int | None = None
    if len(real_attempts) >= 2 and isinstance(latest.score, int) and isinstance(
        first.score, int
    ):
        delta = int(latest.score) - int(first.score)

    # P0-3 — score_history powers the sparkline tooltip on the FE. Capped
    # at 12 entries (most recent) so a power user with hundreds of attempts
    # doesn't bloat the mission-detail payload. Order: oldest → newest.
    history_window = real_attempts[-12:]
    score_history = [
        int(row.score) for row in history_window if isinstance(row.score, int)
    ]

    return YourAttempts(
        count=len(real_attempts),
        best_score=int(best.score) if isinstance(best.score, int) else None,
        best_submission_id=best.submission_id,
        latest_score=int(latest.score) if isinstance(latest.score, int) else None,
        latest_submission_id=latest.submission_id,
        delta=delta,
        best_was_gave_up=best.score_cap_reason == "gave_up",
        score_history=score_history,
    )


__all__ = ["load_your_attempts"]
