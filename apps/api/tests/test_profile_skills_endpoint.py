"""Per-failure-mode mastery aggregation (P2-3).

Drives the skills page on the frontend. The handler groups every mission
by its ``failure_mode`` and reports the logged-in user's attempt count,
pass count, average + best total score. A "pass" is any session whose
``score_report.missed_failure_mode`` is False.

These tests call the handler directly to skip HTTP/auth wiring — the
endpoint registration itself is exercised by the public-profile tests
in ``test_profile_endpoint.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.profiles.router import get_my_skills


async def _bind_engine(db_engine):
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_module.AsyncSessionLocal


def _basic_report(missed: bool, final: int = 24) -> dict:
    return {
        "total": final + 50,
        "dimensions": {
            "final_correctness": {"score": final, "max": 30, "signals": []},
            "verification": {"score": 12, "max": 15, "signals": []},
            "agent_review": {"score": 12, "max": 15, "signals": []},
            "prompt_quality": {"score": 7, "max": 10, "signals": []},
            "context_selection": {"score": 7, "max": 10, "signals": []},
            "safety": {"score": 8, "max": 10, "signals": []},
            "diff_minimality": {"score": 8, "max": 10, "signals": []},
        },
        "strengths": [],
        "weaknesses": [],
        "missed_failure_mode": missed,
        "badges_earned": [],
    }


async def _seed_session(
    *, db, user_id, mission_id, missed, final, completed_at
) -> None:
    sess = SessionRow(
        user_id=user_id,
        mission_id=mission_id,
        status="graded",
        score=final + 50,
        completed_at=completed_at,
    )
    db.add(sess)
    await db.flush()
    db.add(
        Submission(
            session_id=sess.id,
            final_diff="--- a/x\n+++ b/x\n",
            visible_test_results=[],
            hidden_test_results=[],
            validator_results=[],
            score_report=_basic_report(missed=missed, final=final),
            total_score=final + 50,
        )
    )


def _make_mission(*, id_, fm, title) -> Mission:
    return Mission(
        id=id_,
        title=title,
        difficulty="intermediate",
        category="auth",
        repo_pack="some-pack",
        initial_commit="deadbeef",
        estimated_minutes=30,
        failure_mode=fm,
        skills_tested=["x"],
        manifest_sha256="0" * 64,
        published=True,
    )


@pytest.mark.asyncio
async def test_skills_aggregates_attempts_and_passes(db_engine) -> None:
    Session = await _bind_engine(db_engine)
    user_id = uuid.uuid4()
    async with Session() as db:
        user = User(
            id=user_id, email="me@example.com", handle="me", display_name="Me"
        )
        db.add(user)
        db.add(
            _make_mission(
                id_="auth-cookie-expiration",
                fm="checks_presence_not_expiration",
                title="Auth Cookie",
            )
        )
        db.add(
            _make_mission(
                id_="missing-regression-test",
                fm="in_memory_idempotency_only",
                title="Missing Regression",
            )
        )
        await db.flush()
        base = datetime.now(UTC) - timedelta(hours=3)
        # Two attempts on auth: one pass, one fail.
        await _seed_session(
            db=db,
            user_id=user_id,
            mission_id="auth-cookie-expiration",
            missed=False,
            final=28,
            completed_at=base,
        )
        await _seed_session(
            db=db,
            user_id=user_id,
            mission_id="auth-cookie-expiration",
            missed=True,
            final=12,
            completed_at=base + timedelta(hours=1),
        )
        # One attempt on regression, passed.
        await _seed_session(
            db=db,
            user_id=user_id,
            mission_id="missing-regression-test",
            missed=False,
            final=20,
            completed_at=base + timedelta(hours=2),
        )
        await db.commit()

    async with Session() as db:
        user = await db.get(User, user_id)
        catalog = await get_my_skills(user=user, db=db)

    by_fm = {row.failure_mode: row for row in catalog.failure_modes}
    auth = by_fm["checks_presence_not_expiration"]
    # P0-3 / ADR 0009 — the skills view dedupes by mission. Two attempts on
    # the same auth mission collapse to 1 "mission practised" entry; the
    # representative is the best uncapped attempt (28 hidden credit + 50
    # other dims = 78). With a single representative the avg matches the
    # best.
    assert auth.sessions_attempted == 1
    assert auth.sessions_passed == 1
    assert auth.best_score == 78
    assert auth.avg_score == 78.0
    regression = by_fm["in_memory_idempotency_only"]
    assert regression.sessions_attempted == 1
    assert regression.sessions_passed == 1
    assert catalog.total_missions == 2
    assert catalog.total_failure_modes == 2


@pytest.mark.asyncio
async def test_skills_lists_failure_modes_without_attempts(db_engine) -> None:
    """A failure mode the user has never attempted still appears with zeroes."""
    Session = await _bind_engine(db_engine)
    user_id = uuid.uuid4()
    async with Session() as db:
        db.add(
            User(
                id=user_id, email="me@example.com", handle="me", display_name="Me"
            )
        )
        db.add(
            _make_mission(
                id_="security-validation-removed",
                fm="removes_authorization_guard",
                title="Security",
            )
        )
        await db.commit()

    async with Session() as db:
        user = await db.get(User, user_id)
        catalog = await get_my_skills(user=user, db=db)

    assert len(catalog.failure_modes) == 1
    fm = catalog.failure_modes[0]
    assert fm.failure_mode == "removes_authorization_guard"
    assert fm.sessions_attempted == 0
    assert fm.sessions_passed == 0
    assert fm.avg_score is None
    assert fm.best_score is None
    assert fm.mission_ids == ["security-validation-removed"]


@pytest.mark.asyncio
async def test_skills_groups_multiple_missions_per_failure_mode(
    db_engine,
) -> None:
    """When two missions share a failure_mode, the catalog merges them."""
    Session = await _bind_engine(db_engine)
    user_id = uuid.uuid4()
    async with Session() as db:
        db.add(
            User(id=user_id, email="m@e.com", handle="m", display_name="M")
        )
        db.add(_make_mission(id_="m1", fm="cast_via_as_any", title="M1"))
        db.add(_make_mission(id_="m2", fm="cast_via_as_any", title="M2"))
        await db.commit()

    async with Session() as db:
        user = await db.get(User, user_id)
        catalog = await get_my_skills(user=user, db=db)

    fm = catalog.failure_modes[0]
    assert sorted(fm.mission_ids) == ["m1", "m2"]
    assert sorted(fm.mission_titles) == ["M1", "M2"]
    assert catalog.total_failure_modes == 1
    assert catalog.total_missions == 2
