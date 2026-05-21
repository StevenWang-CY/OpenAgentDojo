"""Public profile REST endpoint (plan §13.1).

``GET /api/v1/profiles/{handle}`` is the *only* public surface that exposes a
user's badges + mission history. It is intentionally unauthenticated — anyone
can view any profile by handle. PII (email, user.id) MUST NOT leak.

The endpoint aggregates four things in three round-trips:

  1. Badges  — JOIN ``user_badges`` JOIN ``badges`` ORDER BY ``earned_at DESC``.
  2. History — last 25 graded sessions for the user, JOINed to ``missions``.
  3. Stats   — total graded missions, best score, and per-dimension averages
               over every ``submissions.score_report.dimensions[d].score`` the
               user has ever recorded.

All three queries are scoped by ``user.id`` and return scalar/row tuples so
SQLAlchemy can stream the result without materialising heavy ORM graphs.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.badge import Badge
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.models.user_badge import UserBadge
from app.observability import profile_malformed_reports_total
from app.schemas.profile import (
    EarnedBadgeRead,
    MissionHistoryItemRead,
    PublicProfile,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])

# Plan §11.1 — the seven rubric dimensions.
_RUBRIC_DIMENSIONS: tuple[str, ...] = (
    "final_correctness",
    "verification",
    "agent_review",
    "prompt_quality",
    "context_selection",
    "safety",
    "diff_minimality",
)

# Per-profile history cap. Profile pages render a single table so a large
# cap is wasted bytes; the constant is exported for the test suite.
_HISTORY_LIMIT = 25


async def _fetch_user_by_handle(db: AsyncSession, handle: str) -> User:
    """Look up a user by handle (CITEXT → case-insensitive). 404 on miss."""
    user = (await db.execute(select(User).where(User.handle == handle))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return user


async def _fetch_badges(db: AsyncSession, user_id: uuid.UUID) -> list[EarnedBadgeRead]:
    """Return the user's badges, freshest-first."""
    stmt = (
        select(
            Badge.id,
            Badge.title,
            Badge.description,
            Badge.icon,
            UserBadge.earned_at,
            UserBadge.session_id,
        )
        .join(UserBadge, UserBadge.badge_id == Badge.id)
        .where(UserBadge.user_id == user_id)
        .order_by(UserBadge.earned_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        EarnedBadgeRead(
            id=row.id,
            title=row.title,
            description=row.description,
            icon=row.icon,
            earned_at=row.earned_at,
            session_id=row.session_id,
        )
        for row in rows
    ]


async def _fetch_history(db: AsyncSession, user_id: uuid.UUID) -> list[MissionHistoryItemRead]:
    """Last ``_HISTORY_LIMIT`` graded sessions joined to mission title/difficulty."""
    stmt = (
        select(
            SessionRow.id,
            SessionRow.mission_id,
            SessionRow.completed_at,
            SessionRow.score,
            Mission.title,
            Mission.difficulty,
        )
        .join(Mission, Mission.id == SessionRow.mission_id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.status == "graded",
        )
        .order_by(SessionRow.completed_at.desc().nulls_last(), SessionRow.started_at.desc())
        .limit(_HISTORY_LIMIT)
    )
    rows = (await db.execute(stmt)).all()
    return [
        MissionHistoryItemRead(
            session_id=row.id,
            mission_id=row.mission_id,
            mission_title=row.title,
            completed_at=row.completed_at,
            score=row.score,
            difficulty=row.difficulty,
        )
        for row in rows
    ]


async def _fetch_stats(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[int, int | None, dict[str, float]]:
    """Return (total_missions, best_score, radar_averages).

    ``total_missions`` counts graded sessions with a non-null score.
    ``best_score`` is ``MAX(score)`` across those; ``None`` when there are none.
    ``radar_averages`` is the per-dimension mean of
    ``score_report.dimensions[d].score`` across all of the user's submissions.
    """
    # Single aggregate query for both counts.
    totals_stmt = select(
        func.count(SessionRow.id),
        func.max(SessionRow.score),
    ).where(
        SessionRow.user_id == user_id,
        SessionRow.status == "graded",
        SessionRow.score.is_not(None),
    )
    total_missions, best_score = (await db.execute(totals_stmt)).one()

    # Pull score_report + session_id from each of the user's submissions —
    # the avg has to be computed Python-side because the per-dimension shape
    # is nested JSON ({final_correctness: {score: N, max: M, signals: []}, …})
    # and we want the same arithmetic on Postgres JSONB and SQLite JSON.
    # ``session_id`` is included so the malformed-report observability path
    # can correlate skipped reports back to a session.
    reports_stmt = (
        select(Submission.score_report, Submission.session_id)
        .join(SessionRow, SessionRow.id == Submission.session_id)
        .where(SessionRow.user_id == user_id)
    )
    rows = (await db.execute(reports_stmt)).all()
    reports: list[tuple[Any, uuid.UUID | None]] = [
        (row.score_report, row.session_id) for row in rows
    ]
    radar = _aggregate_radar(reports)
    return int(total_missions or 0), best_score, radar


def _record_malformed(reason: str, session_id: uuid.UUID | None) -> None:
    """Log + meter a score_report that was skipped by ``_aggregate_radar``.

    Logged at ``debug`` so we don't spam production logs when a single bad
    submission slips through; the counter is the durable signal for dashboards.
    """
    logger.debug(
        "profile radar: skipping malformed score_report (reason={}, session_id={})",
        reason,
        session_id,
    )
    profile_malformed_reports_total.labels(reason=reason).inc()


def _aggregate_radar(reports: Sequence[Any]) -> dict[str, float]:
    """Average the per-dimension scores across every submission's score report.

    Each entry in ``reports`` is either a raw ``score_report`` dict (legacy
    callers / tests) or a ``(score_report, session_id)`` tuple. The two shapes
    are accepted so test helpers don't need to fabricate session ids.

    Only the dimensions that actually appeared in at least one submission are
    surfaced — the frontend prefers an empty key to a zero stub. Each value is
    rounded to one decimal place so the radar chart is stable across reloads.
    Malformed reports (non-dict, missing ``dimensions``, non-numeric score) are
    skipped, debug-logged, and counted in
    :data:`app.observability.profile_malformed_reports_total` so dashboards can
    spot scoring-engine drift.
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for entry in reports:
        if isinstance(entry, tuple) and len(entry) == 2:
            report, session_id = entry
        else:
            report, session_id = entry, None
        if not isinstance(report, dict):
            _record_malformed("not_dict", session_id)
            continue
        dims = report.get("dimensions")
        if not isinstance(dims, dict):
            _record_malformed("dimensions_missing", session_id)
            continue
        for dim_name, dim_payload in dims.items():
            if dim_name not in _RUBRIC_DIMENSIONS:
                _record_malformed("unknown_dimension", session_id)
                continue
            if not isinstance(dim_payload, dict):
                _record_malformed("dimension_payload_not_dict", session_id)
                continue
            raw = dim_payload.get("score")
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                _record_malformed("score_not_numeric", session_id)
                continue
            sums[dim_name] = sums.get(dim_name, 0.0) + float(raw)
            counts[dim_name] = counts.get(dim_name, 0) + 1
    return {dim: round(sums[dim] / counts[dim], 1) for dim in sums if counts.get(dim, 0) > 0}


@router.get(
    "/{handle}",
    response_model=PublicProfile,
    summary="Get a user's public profile by handle",
)
async def get_profile(
    handle: str,
    db: AsyncSession = Depends(get_db),
) -> PublicProfile:
    """Return the public profile for ``handle``.

    No authentication required — public surface per §13.1. Returns 404 with
    ``{"detail": "profile not found"}`` when no user owns this handle.
    """
    user = await _fetch_user_by_handle(db, handle)
    badges = await _fetch_badges(db, user.id)
    history = await _fetch_history(db, user.id)
    total_missions, best_score, radar = await _fetch_stats(db, user.id)

    return PublicProfile(
        # ``user.handle`` is nullable in the DB but non-null whenever we got
        # here — _fetch_user_by_handle only finds rows with this handle.
        handle=user.handle or handle,
        display_name=user.display_name,
        joined_at=user.created_at,
        badges=badges,
        history=history,
        radar_averages=radar,
        total_missions=total_missions,
        best_score=best_score,
    )


__all__ = ["router"]
