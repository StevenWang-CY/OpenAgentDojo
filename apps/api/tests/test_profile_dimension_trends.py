"""`GET /profiles/{handle}` returns per-dimension chronological trends (P2-2).

A profile that has graded multiple sessions must expose a per-dimension
score trail so the frontend can render longitudinal sparklines. Pending
scores (``null``) are excluded from the trail — a sparkline must never
plot a number the grader didn't measure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User


async def _bind_engine(db_engine):
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_module.AsyncSessionLocal


def _report(
    final: int | None,
    verification: int | None = 12,
    agent_review: int | None = 12,
    prompt_quality: int | None = 7,
    context_selection: int | None = 7,
    safety: int | None = 8,
    diff_minimality: int | None = 8,
) -> dict:
    dims = {
        "final_correctness": final,
        "verification": verification,
        "agent_review": agent_review,
        "prompt_quality": prompt_quality,
        "context_selection": context_selection,
        "safety": safety,
        "diff_minimality": diff_minimality,
    }
    return {
        "total": sum(v for v in dims.values() if isinstance(v, int)),
        "dimensions": {
            name: {"score": v, "max": 30 if name == "final_correctness" else 10, "signals": []}
            for name, v in dims.items()
        },
        "strengths": [],
        "weaknesses": [],
        "missed_failure_mode": False,
        "badges_earned": [],
    }


@pytest.mark.asyncio
async def test_profile_anonymous_view_omits_dimension_trends(db_engine, client) -> None:
    """Anonymous viewers see the radar averages but not the per-session
    trail — the trail is a fingerprintable skill trajectory and is gated
    behind authenticated self-view."""
    Session = await _bind_engine(db_engine)
    handle = f"user-{uuid.uuid4().hex[:8]}"
    async with Session() as db:
        user = User(handle=handle, email=f"{handle}@example.com", display_name=handle)
        db.add(user)
        await db.flush()
        mission = Mission(
            id="auth-cookie-expiration",
            title="Auth Cookie",
            difficulty="intermediate",
            category="auth",
            repo_pack="fullstack-auth-demo",
            initial_commit="deadbeef",
            estimated_minutes=30,
            failure_mode="checks_presence_not_expiration",
            skills_tested=["auth"],
            manifest_sha256="0" * 64,
            expected_weak_dim="safety",
        )
        db.add(mission)
        await db.flush()
        base = datetime.now(UTC) - timedelta(hours=3)
        for i, score in enumerate([20, 24, 28]):
            sess = SessionRow(
                user_id=user.id,
                mission_id="auth-cookie-expiration",
                status="graded",
                score=score,
                completed_at=base + timedelta(hours=i),
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
                    score_report=_report(final=score),
                    total_score=score,
                )
            )
        await db.commit()

    resp = await client.get(f"/api/v1/profiles/{handle}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Anonymous viewer → trends omitted (privacy gate).
    assert body.get("dimension_trends") == {}, (
        f"anonymous viewer must not receive per-session trail; got {body.get('dimension_trends')!r}"
    )
    # Radar averages remain public so the public profile is still
    # informative.
    assert "radar_averages" in body


@pytest.mark.asyncio
async def test_profile_trends_exclude_pending_scores(db_engine, client) -> None:
    """The trends aggregator excludes pending scores — verified at the
    handler level via _fetch_dimension_trends since the public route
    gates the field behind self-view."""
    from app.profiles.router import _fetch_dimension_trends

    Session = await _bind_engine(db_engine)
    handle = f"user-{uuid.uuid4().hex[:8]}"
    async with Session() as db:
        user = User(handle=handle, email=f"{handle}@example.com", display_name=handle)
        db.add(user)
        await db.flush()
        mission = Mission(
            id="missing-regression-test",
            title="Missing Regression Test",
            difficulty="intermediate",
            category="testing",
            repo_pack="some-pack",
            initial_commit="deadbeef",
            estimated_minutes=30,
            failure_mode="in_memory_set_guard",
            skills_tested=["regression-tests"],
            manifest_sha256="0" * 64,
            expected_weak_dim="safety",
        )
        db.add(mission)
        await db.flush()
        base = datetime.now(UTC) - timedelta(hours=2)
        # First session: prompt_quality measured (7).
        # Second session: prompt_quality pending (null).
        # Third session: prompt_quality measured (9).
        for i, pq in enumerate([7, None, 9]):
            sess = SessionRow(
                user_id=user.id,
                mission_id="missing-regression-test",
                status="graded",
                score=80,
                completed_at=base + timedelta(hours=i),
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
                    score_report=_report(final=24, prompt_quality=pq),
                    total_score=80,
                )
            )
        await db.commit()

    # Call the aggregator directly (route gates dimension_trends behind
    # self-view, which isn't easily reproduced in this test).
    async with Session() as db:
        user = (await db.execute(select(User).where(User.handle == handle))).scalar_one()
        trends = await _fetch_dimension_trends(db, user.id)
    pq_trail = trends.get("prompt_quality") or []
    # The pending session is dropped — only sessions 1 and 3 appear.
    assert [p.score for p in pq_trail] == [7, 9], pq_trail


@pytest.mark.asyncio
async def test_profile_with_no_history_has_empty_trends(db_engine, client) -> None:
    Session = await _bind_engine(db_engine)
    handle = f"user-{uuid.uuid4().hex[:8]}"
    async with Session() as db:
        user = User(handle=handle, email=f"{handle}@example.com", display_name=handle)
        db.add(user)
        await db.commit()

    resp = await client.get(f"/api/v1/profiles/{handle}")
    body = resp.json()
    assert body.get("dimension_trends") == {}
