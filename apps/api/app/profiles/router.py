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
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_auth
from app.db.session import get_db
from app.grading.attempts import candidate_beats
from app.grading.dimensions import DIMENSION_NAMES
from app.models.badge import Badge
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.models.user_badge import UserBadge
from app.observability import profile_malformed_reports_total
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
    """Last ``_HISTORY_LIMIT`` graded sessions joined to mission title/difficulty.

    Filters out grader-failure stubs (``score_report.is_stub == True``) so the
    public history doesn't surface 0/100 rows that the radar deliberately
    excludes via :func:`_best_per_mission`. ``is_stub`` lives in the
    ``score_report`` JSONB column rather than a dedicated column, so the
    skip is applied in Python after a small over-fetch.
    """
    stmt = (
        select(
            SessionRow.id,
            SessionRow.mission_id,
            SessionRow.completed_at,
            SessionRow.score,
            Mission.title,
            Mission.difficulty,
            Submission.score_report,
        )
        .join(Mission, Mission.id == SessionRow.mission_id)
        .outerjoin(Submission, Submission.session_id == SessionRow.id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.status == "graded",
        )
        .order_by(SessionRow.completed_at.desc().nulls_last(), SessionRow.started_at.desc())
        # Over-fetch so we still return _HISTORY_LIMIT rows after stubs are
        # filtered out; cap at 3x to keep the query cheap.
        .limit(_HISTORY_LIMIT * 3)
    )
    raw = (await db.execute(stmt)).all()
    rows = []
    for row in raw:
        report = row.score_report
        if isinstance(report, dict) and report.get("is_stub"):
            continue
        rows.append(row)
        if len(rows) >= _HISTORY_LIMIT:
            break
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
) -> tuple[
    int,
    int | None,
    dict[str, float],
    dict[str, float] | None,
    bool,
    bool,
]:
    """Return (total_missions, best_score, radar_averages, dimension_history_verified, has_verified_attempts, verified_attempts_only).

    P0-3 — public aggregates use **best-per-mission**, not "average across
    every submission". The per-mission best is selected with the policy
    documented in ADR 0009:

      * Tier 1: best uncapped attempt (score_cap_reason IS NULL).
      * Tier 2: best gave-up attempt — only when no uncapped attempt exists.

    P0-8 — the public radar partitions on ``submission.verified``. When
    at least one verified (proctored) best-per-mission attempt exists,
    ``radar_averages`` is built from the verified bucket only and
    ``verified_attempts_only`` is True; the FE renders the "verified"
    chip and offers a toggle. When no verified attempts exist, the
    radar falls back to the every-attempt bucket so the page isn't
    empty — paired with ``has_verified_attempts: false`` so the FE can
    show the honor-mode notice.
    ``dimension_history_verified`` always carries the verified-only
    aggregate when ``has_verified_attempts`` is true, regardless of
    which bucket ``radar_averages`` ends up holding. This lets the FE
    flip between buckets without a refetch.

    Observability: ``_aggregate_radar`` reports malformed payloads on
    every (submission, session) tuple it sees. We pre-walk every graded
    submission so the malformed-report counter still increments for
    duplicates that lost the best-per-mission tiebreaker. Without this
    extra pass, a malformed shape that only ever appears on a "loser"
    submission would slip past the alarm.
    """
    bests = await _best_per_mission(db, user_id)
    total_missions = len(bests)
    best_score = max((b.score for b in bests if b.score is not None), default=None)

    # P0-8 — partition the best-per-mission set on ``verified``. The
    # verified subset drives the canonical credentialing radar; the
    # full set is the honest fallback when no verified attempts exist.
    verified_bests = [b for b in bests if b.verified]
    has_verified_attempts = len(verified_bests) > 0

    if has_verified_attempts:
        primary_inputs: list[tuple[Any, uuid.UUID | None]] = [
            (b.score_report, b.session_id) for b in verified_bests
        ]
        radar = _aggregate_radar(primary_inputs)
        verified_radar: dict[str, float] | None = radar
        verified_attempts_only = True
    else:
        all_inputs: list[tuple[Any, uuid.UUID | None]] = [
            (b.score_report, b.session_id) for b in bests
        ]
        radar = _aggregate_radar(all_inputs)
        verified_radar = None
        verified_attempts_only = False

    # Independent malformed-report sweep: walk EVERY graded submission so
    # the observability counter doesn't silently lose signal when a
    # malformed report happens to be the loser in best-per-mission
    # tie-breaking. The radar itself is unaffected — this run is for
    # the metric only.
    await _observe_malformed_across_all_submissions(db, user_id, skip=bests)

    return (
        int(total_missions or 0),
        best_score,
        radar,
        verified_radar,
        has_verified_attempts,
        verified_attempts_only,
    )


async def _observe_malformed_across_all_submissions(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    skip: Sequence[_BestAttempt],
) -> None:
    """Re-walk every non-best submission and bump the malformed counter.

    The best-per-mission submissions have already been measured by the
    primary ``_aggregate_radar`` call; we deliberately exclude them here
    to avoid double-counting. Anything else with a malformed shape is
    surfaced into the same Prometheus counter.
    """
    skip_ids = {b.session_id for b in skip}
    stmt = (
        select(Submission.score_report, Submission.session_id)
        .join(SessionRow, SessionRow.id == Submission.session_id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.status == "graded",
        )
    )
    rows = (await db.execute(stmt)).all()
    extras: list[tuple[Any, uuid.UUID | None]] = []
    for row in rows:
        if row.session_id in skip_ids:
            continue
        extras.append((row.score_report, row.session_id))
    if extras:
        # ``_aggregate_radar`` returns a dict we ignore — its side effect
        # is the per-malformed-row metric increment we care about.
        _aggregate_radar(extras)


@dataclass(frozen=True)
class _BestAttempt:
    """A single user's best graded attempt against one mission (P0-3 ADR 0009).

    Frozen for hashability and to make the read-only access patterns
    explicit. Carries everything the radar aggregator, the skills-view, and
    the mission-detail ``your_attempts`` strip need without re-querying.
    """

    mission_id: str
    session_id: uuid.UUID
    submission_id: uuid.UUID
    score: int | None
    score_report: Any
    completed_at: Any
    score_cap_reason: str | None
    # P0-8 — true iff this submission's producing session was proctored at
    # grade time. Lets the public profile partition the radar without a
    # second query. Defaults False so legacy fixtures that don't construct
    # the field keep working.
    verified: bool = False


async def _best_per_mission(db: AsyncSession, user_id: uuid.UUID) -> list[_BestAttempt]:
    """For each mission, return the user's best attempt (P0-3 policy).

    Selection rule (per ADR 0009):
      1. Exclude grader-failure stubs (``score_report.is_stub == True``).
      2. Among the remaining attempts on the same mission, prefer uncapped
         over gave-up.
      3. Within the preferred tier, the highest ``total_score`` wins; ties
         break to the most recent ``completed_at``.

    The selection is computed Python-side (not as a window function in
    SQL) so the policy stays portable across the SQLite-backed tests and
    Postgres production — and so the stub-exclusion can read the JSONB
    flag uniformly. The N here is bounded by the user's total graded
    attempts; even a power user with hundreds of attempts is comfortably
    sub-millisecond.
    """
    stmt = (
        select(
            SessionRow.id.label("session_id"),
            SessionRow.mission_id.label("mission_id"),
            SessionRow.completed_at.label("completed_at"),
            SessionRow.score.label("score"),
            Submission.id.label("submission_id"),
            Submission.score_report.label("score_report"),
            Submission.score_cap_reason.label("score_cap_reason"),
            # P0-8 — pull the verified bool so the radar partition is
            # built from one query, not two.
            Submission.verified.label("verified"),
        )
        .join(Submission, Submission.session_id == SessionRow.id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.status == "graded",
        )
        .order_by(SessionRow.completed_at.desc().nullslast(), SessionRow.id.desc())
    )
    rows = (await db.execute(stmt)).all()

    # Bucket by mission, applying tier preference + score ordering.
    by_mission: dict[str, _BestAttempt] = {}
    for row in rows:
        report = row.score_report
        if isinstance(report, dict) and report.get("is_stub"):
            continue
        candidate = _BestAttempt(
            mission_id=str(row.mission_id),
            session_id=row.session_id,
            submission_id=row.submission_id,
            score=row.score,
            score_report=report,
            completed_at=row.completed_at,
            score_cap_reason=row.score_cap_reason,
            verified=bool(getattr(row, "verified", False)),
        )
        current = by_mission.get(candidate.mission_id)
        if current is None or candidate_beats(candidate, current):
            by_mission[candidate.mission_id] = candidate
    return list(by_mission.values())


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
                DimensionTrendPoint(completed_at=completed_at, score=int(raw))
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
async def get_profile(
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
    (
        total_missions,
        best_score,
        radar,
        verified_radar,
        has_verified_attempts,
        verified_attempts_only,
    ) = await _fetch_stats(db, user.id)
    # Self-view only: per-session score trail can be combined with the
    # mission history to reconstruct a complete supervision profile, so
    # we gate it behind authenticated self-access.
    is_self_view = viewer is not None and viewer.id == user.id
    dimension_trends = await _fetch_dimension_trends(db, user.id) if is_self_view else {}

    return PublicProfile(
        # ``user.handle`` is nullable in the DB but non-null whenever we got
        # here — _fetch_user_by_handle only finds rows with this handle.
        handle=user.handle or handle,
        display_name=user.display_name,
        joined_at=user.created_at,
        # P0-7 — surface the GitHub OAuth verification state on the public
        # profile. The FE renders the verified chip when ``github_verified_at``
        # is non-null, and the "self-attested" chip otherwise. Nothing here
        # leaks PII — ``github_login`` and ``github_html_url`` are
        # already-public data on github.com.
        github_login=user.github_login,
        github_avatar_url=user.github_avatar_url,
        github_html_url=user.github_html_url,
        github_verified_at=user.github_verified_at,
        badges=badges,
        history=history,
        radar_averages=radar,
        # P0-8 — verified bucket + partition flags. The FE renders the
        # default "Verified only" radar when ``has_verified_attempts`` is
        # true and offers the user a toggle into the "all attempts"
        # bucket (which surfaces a "Includes honor-mode practice scores"
        # notice).
        dimension_history_verified=verified_radar,
        has_verified_attempts=has_verified_attempts,
        verified_attempts_only=verified_attempts_only,
        dimension_trends=dimension_trends,
        total_missions=total_missions,
        best_score=best_score,
    )


def _skills_dedupe_by_mission(rows: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Collapse a user's per-mission graded attempts to one best entry each.

    Implements the same tier policy as :func:`_best_per_mission` (uncapped
    beats capped; within tier, higher ``score`` wins; ties break to the
    more recent ``completed_at``). Pulled out as a helper so
    :func:`get_my_skills` stays under the ruff PLR0912/PLR0915 limits and
    so the policy lives in one place — drift between the radar and the
    skills view is the kind of silent bug that's expensive to catch
    after the fact.

    Each input ``row`` is expected to expose ``mission_id``, ``score``,
    ``completed_at``, ``score_cap_reason``, ``failure_mode``, and
    ``score_report``. Grader-failure stubs (``score_report.is_stub``) are
    excluded so a transient sandbox outage doesn't make the user appear
    to have practised a mission they never really attempted.
    """
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        report = row.score_report
        if isinstance(report, dict) and report.get("is_stub"):
            continue
        mid = str(row.mission_id)
        candidate = {
            "failure_mode": row.failure_mode,
            "score_report": report,
            "score": row.score,
            "completed_at": row.completed_at,
            "score_cap_reason": row.score_cap_reason,
        }
        current = best.get(mid)
        if current is None or candidate_beats(candidate, current):
            best[mid] = candidate
    return best


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

    # P0-3 — Pull every graded attempt, then collapse to best-per-mission so
    # "sessions_attempted" and "sessions_passed" reflect missions practised
    # (not raw row count). A user with 3 attempts on the same mission
    # contributes 1 to attempted, and 1 to passed iff their best uncapped
    # attempt cleared the threshold. Failure-mode rollups sum across the
    # best-per-mission set.
    #
    # Excluding non-graded sessions so error / abandoned attempts don't
    # inflate the "attempted" counter. Stubs are filtered inside
    # ``_best_per_mission``.
    sessions_stmt = (
        select(
            Mission.failure_mode,
            Submission.score_report,
            SessionRow.id.label("session_id"),
            SessionRow.mission_id,
            SessionRow.score,
            SessionRow.completed_at,
            Submission.score_cap_reason,
        )
        .join(SessionRow, SessionRow.mission_id == Mission.id)
        .join(Submission, Submission.session_id == SessionRow.id)
        .where(
            SessionRow.user_id == user.id,
            SessionRow.status == "graded",
        )
        .order_by(SessionRow.completed_at.desc().nullslast(), SessionRow.id.desc())
    )
    rows = (await db.execute(sessions_stmt)).all()
    best_per_mission = _skills_dedupe_by_mission(rows)

    attempts: dict[str, dict[str, Any]] = {}
    for entry in best_per_mission.values():
        report = entry["score_report"]
        fm = entry["failure_mode"] or "unknown"
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
        score = entry["score"]
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
        completed_at = entry["completed_at"]
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
                avg_score=(round(sum(scores) / len(scores), 1) if scores else None),
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
