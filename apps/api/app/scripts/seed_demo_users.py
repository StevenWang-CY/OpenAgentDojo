"""Seed three demo profiles so ``/profile/{handle}`` has content to render.

Satisfies the §25 DoD bullet "Public profile page renders for at least 3 demo
users with realistic histories". Idempotent — re-running upserts cleanly and
never duplicates rows.

The script is *intentionally* a separate module rather than an Alembic
migration: it depends on existing rows in ``missions`` and ``badges`` (seeded
by ``0002_seed_badges`` and ``0003_seed_missions``) and we want to be able
to re-run it after a fresh DB without re-running migrations.

Refuses to run when ``ARENA_ENV=production`` so demo accounts never leak into
a real deployment.

Usage::

    # Local dev (compose up):
    cd apps/api
    uv run python -m app.scripts.seed_demo_users

    # Or via the dev helper:
    bash infra/scripts/seed_dev.sh   # invokes this script as the last step
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session as _session_module
from app.models.badge import Badge
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.models.user_badge import UserBadge

# Stable UUIDs so re-runs upsert deterministically. Generated once via
# ``uuid.UUID('… random …')``; pinned here so demo URLs are stable across
# environments and screenshots.
_DEMO_USERS: list[dict[str, Any]] = [
    {
        "id": uuid.UUID("a1ce0000-0000-4000-8000-000000000001"),
        "email": "alice@arena.demo",
        "handle": "alice",
        "display_name": "Alice Liddell",
        "joined_days_ago": 90,
        "missions": [
            # (mission_id, score, badges, days_ago)
            ("auth-cookie-expiration", 92, ["regression-test-writer"], 7),
            ("security-validation-removed", 87, ["security-aware-reviewer"], 14),
            ("agent-wrong-file", 78, ["agent-skeptic"], 28),
            ("api-contract-drift", 81, ["api-contract-guardian"], 42),
        ],
    },
    {
        "id": uuid.UUID("b0b00000-0000-4000-8000-000000000002"),
        "email": "bob@arena.demo",
        "handle": "bob",
        "display_name": "Bob Sutton",
        "joined_days_ago": 45,
        "missions": [
            ("missing-regression-test", 88, ["regression-test-writer"], 3),
            ("overfitted-test-fix", 71, [], 10),
            ("async-race-condition", 95, ["concurrency-debugger"], 20),
        ],
    },
    {
        "id": uuid.UUID("ca7c0000-0000-4000-8000-000000000003"),
        "email": "carol@arena.demo",
        "handle": "carol",
        "display_name": "Carol Tan",
        "joined_days_ago": 14,
        "missions": [
            ("excessive-rewrite", 84, ["minimal-diff"], 2),
            ("dependency-misuse", 67, [], 5),
        ],
    },
]


def _score_dimensions(total: int) -> dict[str, dict[str, Any]]:
    """Distribute ``total`` across the 7 rubric dimensions in a plausible shape.

    Used so the radar chart on the profile page renders with realistic
    proportions rather than every demo user having identical scores.
    """
    # Plan §11.1 weights — sourced from the single source of truth in
    # :mod:`app.grading.dimensions` so the demo radar uses the same weights
    # the live scoring engine does.
    from app.grading.dimensions import DIMENSION_MAX

    weights = dict(DIMENSION_MAX)
    # Scale each dim proportional to the user's overall score (total/100), then
    # round to int and clamp to the dim's max.
    pct = total / 100.0
    return {
        name: {
            "score": min(max_pts, round(max_pts * pct)),
            "max": max_pts,
            "signals": [f"demo score for {name}"],
        }
        for name, max_pts in weights.items()
    }


async def _upsert_user(db: AsyncSession, spec: dict[str, Any]) -> User:
    user = await db.get(User, spec["id"])
    joined_at = datetime.now(UTC) - timedelta(days=spec["joined_days_ago"])
    if user is None:
        user = User(
            id=spec["id"],
            email=spec["email"],
            handle=spec["handle"],
            display_name=spec["display_name"],
            created_at=joined_at,
            last_login_at=joined_at,
        )
        db.add(user)
    else:
        user.email = spec["email"]
        user.handle = spec["handle"]
        user.display_name = spec["display_name"]
        user.created_at = joined_at
        user.last_login_at = joined_at
    return user


async def _seed_mission_history(
    db: AsyncSession,
    user: User,
    user_spec: dict[str, Any],
) -> int:
    """Create one ``SessionRow`` + ``Submission`` per declared mission.

    Returns the count of sessions written. Existing demo sessions for this
    user are deleted first so re-runs produce a clean state.
    """
    # Drop any prior demo sessions for this user so we don't accumulate.
    prior = (
        (await db.execute(select(SessionRow.id).where(SessionRow.user_id == user.id)))
        .scalars()
        .all()
    )
    for sid in prior:
        sub = (
            await db.execute(select(Submission).where(Submission.session_id == sid))
        ).scalar_one_or_none()
        if sub is not None:
            await db.delete(sub)
        sess = await db.get(SessionRow, sid)
        if sess is not None:
            await db.delete(sess)
    await db.flush()

    count = 0
    for mission_id, score, badge_ids, days_ago in user_spec["missions"]:
        # Verify mission exists — log + skip otherwise so a stale demo entry
        # never breaks the seed.
        mission = await db.get(Mission, mission_id)
        if mission is None:
            logger.warning("[seed-demo] mission '{}' not found; skipping", mission_id)
            continue

        completed_at = datetime.now(UTC) - timedelta(days=days_ago)
        started_at = completed_at - timedelta(minutes=35)

        session_row = SessionRow(
            id=uuid.uuid4(),
            user_id=user.id,
            mission_id=mission_id,
            status="graded",
            started_at=started_at,
            last_activity_at=completed_at,
            completed_at=completed_at,
            score=score,
            agent_turns=2,
        )
        db.add(session_row)
        await db.flush()

        dims = _score_dimensions(score)
        score_report = {
            "total": score,
            "dimensions": dims,
            "strengths": ["Selected relevant context", "Wrote a regression test"],
            "weaknesses": ["Could have run more verification"] if score < 90 else [],
            "missed_failure_mode": score < 80,
            "badges_earned": list(badge_ids),
        }

        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=session_row.id,
                final_diff=f"# demo final diff for {mission_id}\n",
                # Lists to match the shared-types contract — each entry is a
                # serialised ``TestRunResult`` / ``ValidatorResult`` dict that
                # the FE iterates as an array.
                visible_test_results=[{"suite": "unit", "passed": 5, "failed": 0, "exit_code": 0}],
                hidden_test_results=[
                    {
                        "suite": "hidden",
                        "passed": 4,
                        "failed": 1 if score < 90 else 0,
                        "exit_code": 0 if score >= 90 else 1,
                    }
                ],
                validator_results=[{"kind": "forbidden_changes", "passed": True}],
                score_report=score_report,
                total_score=score,
                created_at=completed_at,
            )
        )

        # Badges earned for this session.
        for badge_id in badge_ids:
            badge = await db.get(Badge, badge_id)
            if badge is None:
                logger.warning("[seed-demo] badge '{}' not in catalog; skipping award", badge_id)
                continue
            existing = await db.get(UserBadge, (user.id, badge_id))
            if existing is None:
                db.add(
                    UserBadge(
                        user_id=user.id,
                        badge_id=badge_id,
                        earned_at=completed_at,
                        session_id=session_row.id,
                    )
                )

        count += 1

    return count


async def seed_demo_users() -> int:
    """Insert / refresh the three demo profiles. Returns total session count.

    Refuses to run in production. Caller is expected to have applied
    migrations and to have a running DB.
    """
    settings = get_settings()
    if settings.arena_env == "production":
        raise RuntimeError("refusing to seed demo users in ARENA_ENV=production")

    total_sessions = 0
    # Resolve the session factory at call time so test rebinds via
    # ``session_module.AsyncSessionLocal = …`` are honoured.
    async with _session_module.AsyncSessionLocal() as db:
        for spec in _DEMO_USERS:
            user = await _upsert_user(db, spec)
            await db.flush()
            count = await _seed_mission_history(db, user, spec)
            total_sessions += count
            logger.info(
                "[seed-demo] user '{}' seeded with {} graded sessions",
                spec["handle"],
                count,
            )
        await db.commit()
    return total_sessions


def main() -> int:
    try:
        total = asyncio.run(seed_demo_users())
    except RuntimeError as exc:
        print(f"seed_demo_users: {exc}", file=sys.stderr)
        return 1
    print(f"seeded {len(_DEMO_USERS)} demo users with {total} total graded sessions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
