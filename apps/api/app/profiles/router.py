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
from app.grading.dimensions import DIMENSION_NAMES
from app.models.badge import Badge
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.models.user_badge import UserBadge
from app.observability import profile_malformed_reports_total
from app.auth.deps import get_current_user, require_auth
from app.schemas.profile import (
    DimensionTrendPoint,
    EarnedBadgeRead,
    FailureModeMastery,
    MissionHistoryItemRead,
    PublicProfile,
    SkillsCatalog,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])

# Plan §11.1 — the seven rubric dimensions. Sourced from the single
# ``app.grading.dimensions`` table so the radar aggregator can never drift
# from the scoring engine's actual outputs.
_RUBRIC_DIMENSIONS: tuple[str, ...] = DIMENSION_NAMES

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

    # Pull score_report + session_id + completed_at from each of the user's
    # submissions — the avg has to be computed Python-side because the
    # per-dimension shape is nested JSON ({final_correctness: {score: N,
    # max: M, signals: []}, …}) and we want the same arithmetic on Postgres
    # JSONB and SQLite JSON. ``session_id`` is included so the
    # malformed-report observability path can correlate skipped reports
    # back to a session. ``completed_at`` drives the per-dimension
    # longitudinal sparklines (P2-2).
    reports_stmt = (
        select(
            Submission.score_report,
            Submission.session_id,
            SessionRow.completed_at,
        )
        .join(SessionRow, SessionRow.id == Submission.session_id)
        .where(
            SessionRow.user_id == user_id,
            # Only graded sessions contribute to the radar — error /
            # provisioning / abandoned stubs would otherwise drag every
            # average toward zero.
            SessionRow.status == "graded",
        )
        .order_by(SessionRow.completed_at.asc().nullslast())
    )
    rows = (await db.execute(reports_stmt)).all()
    reports: list[tuple[Any, uuid.UUID | None]] = [
        (row.score_report, row.session_id) for row in rows
    ]
    radar = _aggregate_radar(reports)
    return int(total_missions or 0), best_score, radar


async def _fetch_dimension_trends(
    db: AsyncSession, user_id: uuid.UUID
) -> dict[str, list[DimensionTrendPoint]]:
    """Build per-dimension chronological score trails for the sparklines.

    Pending scores (``null`` — measurement unavailable) are skipped so the
    sparkline never claims a number the grader didn't measure. Sessions
    without ``completed_at`` are skipped too — they would have no x-axis.
    """
    stmt = (
        select(
            Submission.score_report,
            Submission.session_id,
            SessionRow.completed_at,
        )
        .join(SessionRow, SessionRow.id == Submission.session_id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.completed_at.is_not(None),
            # Same exclusion as the radar: error / abandoned stubs would
            # otherwise leak synthetic zero points onto the sparkline.
            SessionRow.status == "graded",
        )
        .order_by(SessionRow.completed_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    trends: dict[str, list[DimensionTrendPoint]] = {}
    for row in rows:
        report = row.score_report
        completed_at = row.completed_at
        if not isinstance(report, dict) or completed_at is None:
            continue
        # Stub reports (grader-failure backstop) carry score=0 and would
        # show up as a hard "dropped to zero" tick on every sparkline.
        if report.get("is_stub"):
            continue
        dims = report.get("dimensions")
        if not isinstance(dims, dict):
            continue
        for dim_name in _RUBRIC_DIMENSIONS:
            payload = dims.get(dim_name)
            if not isinstance(payload, dict):
                continue
            raw = payload.get("score")
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                # Skipping null (pending) or non-numeric — both unmeasurable.
                continue
            trends.setdefault(dim_name, []).append(
                DimensionTrendPoint(
                    completed_at=completed_at, score=int(raw)
                )
            )
    return trends


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
        # Skip grader-failure stubs entirely — they carry score=0 across the
        # board but represent infrastructure failures, not real attempts.
        # Counting them would drag the radar toward zero for any user
        # unlucky enough to hit a transient sandbox/LLM outage.
        if report.get("is_stub"):
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
            if raw is None:
                # Pending dimension (judge cache cold + LLM unavailable).
                # Not an error — just unmeasurable on this run.
                _record_malformed("score_pending", session_id)
                continue
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
async def get_profile(  # noqa: PLR0912 — guarded by `/me/skills` route below
    handle: str,
    db: AsyncSession = Depends(get_db),
    viewer: User | None = Depends(get_current_user),
) -> PublicProfile:
    """Return the public profile for ``handle``.

    No authentication required — public surface per §13.1. Returns 404 with
    ``{"detail": "profile not found"}`` when no user owns this handle.

    The detailed per-dimension longitudinal trail is only returned to the
    profile owner themselves — anonymous and other-user viewers see the
    radar averages but not the trend per session. This avoids leaking a
    fingerprintable skill trajectory of any user by handle.
    """
    user = await _fetch_user_by_handle(db, handle)
    badges = await _fetch_badges(db, user.id)
    history = await _fetch_history(db, user.id)
    total_missions, best_score, radar = await _fetch_stats(db, user.id)
    # Self-view only: per-session score trail can be combined with the
    # mission history to reconstruct a complete supervision profile, so
    # we gate it behind authenticated self-access.
    is_self_view = viewer is not None and viewer.id == user.id
    dimension_trends = (
        await _fetch_dimension_trends(db, user.id) if is_self_view else {}
    )

    return PublicProfile(
        # ``user.handle`` is nullable in the DB but non-null whenever we got
        # here — _fetch_user_by_handle only finds rows with this handle.
        handle=user.handle or handle,
        display_name=user.display_name,
        joined_at=user.created_at,
        badges=badges,
        history=history,
        radar_averages=radar,
        dimension_trends=dimension_trends,
        total_missions=total_missions,
        best_score=best_score,
    )


@router.get(
    "/me/skills",
    response_model=SkillsCatalog,
    summary="Per-failure-mode mastery summary for the logged-in user",
)
async def get_my_skills(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SkillsCatalog:
    """Return the failure-mode catalog with the user's attempt/pass stats.

    The catalog groups the 10 supervision failure modes (one per mission)
    and reports, for each, the user's attempt count, pass count, and
    average + best total score. A "pass" is a session whose score_report
    has ``missed_failure_mode == False``.
    """
    # Pull every mission so the catalog renders even when the user has
    # never attempted some failure modes.
    missions_stmt = select(Mission).where(Mission.published.is_(True))
    missions = (await db.execute(missions_stmt)).scalars().all()
    by_failure_mode: dict[str, dict[str, Any]] = {}
    for m in missions:
        fm = m.failure_mode or "unknown"
        entry = by_failure_mode.setdefault(
            fm,
            {
                "mission_ids": [],
                "mission_titles": [],
                "title": fm.replace("_", " ").title(),
            },
        )
        entry["mission_ids"].append(m.id)
        entry["mission_titles"].append(m.title)

    # Pull the user's graded sessions joined to mission failure_mode.
    # Excluding non-graded sessions so error / abandoned attempts don't
    # inflate the "attempted" counter.
    sessions_stmt = (
        select(
            Mission.failure_mode,
            Submission.score_report,
            SessionRow.score,
            SessionRow.completed_at,
        )
        .join(SessionRow, SessionRow.mission_id == Mission.id)
        .join(Submission, Submission.session_id == SessionRow.id)
        .where(
            SessionRow.user_id == user.id,
            SessionRow.status == "graded",
        )
    )
    rows = (await db.execute(sessions_stmt)).all()
    attempts: dict[str, dict[str, Any]] = {}
    for row in rows:
        report = row.score_report
        # Grader-failure stubs aren't real attempts — exclude them so a
        # transient sandbox outage doesn't make the user appear to have
        # tried (and failed) every mission they touched.
        if isinstance(report, dict) and report.get("is_stub"):
            continue
        fm = row.failure_mode or "unknown"
        slot = attempts.setdefault(
            fm,
            {
                "attempted": 0,
                "passed": 0,
                "scores": [],
                "last_attempted_at": None,
            },
        )
        slot["attempted"] += 1
        score = row.score
        if isinstance(score, int):
            slot["scores"].append(score)
        # "Pass" = the supervisor materially caught the agent's failure
        # mode. Two signals must hold: (a) the score crossed a meaningful
        # threshold (>= 70% of the *effective max* for this report, i.e.
        # not just a fluke partial-credit), AND (b) the rubric flagged
        # the failure mode as caught (missed_failure_mode == False).
        # Previously we relied only on (b), but with proportional hidden
        # credit a 9/10-hidden-passing submission is True for (a) yet
        # still has missed_failure_mode==True under the binary
        # _hidden_tests_passed predicate — the user gets no mastery
        # credit for substantial supervision.
        if isinstance(report, dict):
            effective_max = (
                int(report.get("effective_max") or 100)
                if isinstance(report.get("effective_max"), int)
                else 100
            )
            score_threshold = max(1, int(effective_max * 0.7))
            score_passed = isinstance(score, int) and score >= score_threshold
            missed_failure_mode = bool(report.get("missed_failure_mode", True))
            if score_passed or not missed_failure_mode:
                slot["passed"] += 1
        completed_at = row.completed_at
        if completed_at is not None:
            cur = slot["last_attempted_at"]
            if cur is None or completed_at > cur:
                slot["last_attempted_at"] = completed_at

    mastery: list[FailureModeMastery] = []
    for fm, meta in sorted(by_failure_mode.items()):
        a = attempts.get(fm, {})
        scores = a.get("scores", [])
        mastery.append(
            FailureModeMastery(
                failure_mode=fm,
                failure_mode_title=meta["title"],
                mission_ids=meta["mission_ids"],
                mission_titles=meta["mission_titles"],
                sessions_attempted=a.get("attempted", 0),
                sessions_passed=a.get("passed", 0),
                avg_score=(
                    round(sum(scores) / len(scores), 1) if scores else None
                ),
                best_score=max(scores) if scores else None,
                last_attempted_at=a.get("last_attempted_at"),
            )
        )

    return SkillsCatalog(
        failure_modes=mastery,
        total_missions=len(missions),
        total_failure_modes=len(by_failure_mode),
    )


__all__ = ["router"]
