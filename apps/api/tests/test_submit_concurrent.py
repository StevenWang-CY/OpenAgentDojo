"""Concurrent-submit race protection (P0-B2).

A second submit request while the first is mid-flight must be rejected with
409 — both at the cheap pre-check ("session is already submitting") and at
the atomic UPDATE guard ("active → submitting"). This test pins the second
behaviour: two parallel claim attempts must produce exactly one winner.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User


@pytest.mark.asyncio
async def test_atomic_claim_only_one_winner(db_engine) -> None:
    """Two parallel `_claim_for_submit` attempts must produce one True + one False."""
    from app.sessions.submit import _claim_for_submit

    user_id = uuid.uuid4()
    sid = uuid.uuid4()
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async with factory() as db:
        db.add(
            User(id=user_id, email=f"c-{user_id}@arena.local", handle=f"c{str(user_id)[:6]}")
        )
        db.add(
            Mission(
                id="claim-test-mission",
                title="x",
                difficulty="beginner",
                category="testing",
                repo_pack="pack",
                initial_commit="abc",
                estimated_minutes=5,
                failure_mode="none",
                skills_tested=[],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=sid,
                user_id=user_id,
                mission_id="claim-test-mission",
                status="active",
            )
        )
        await db.commit()

    async def _attempt() -> bool:
        async with factory() as db:
            session = await db.get(SessionRow, sid)
            won = await _claim_for_submit(db, session)
            if won:
                await db.commit()
            return won

    results = await asyncio.gather(_attempt(), _attempt())
    assert sum(1 for r in results if r) == 1, results
    assert sum(1 for r in results if not r) == 1, results

    async with factory() as db:
        row = await db.get(SessionRow, sid)
        assert row.status == "submitting"


@pytest.mark.asyncio
async def test_claim_no_op_when_status_not_active(db_engine) -> None:
    """Already-submitting / graded / error rows MUST NOT be re-claimable."""
    from app.sessions.submit import _claim_for_submit

    user_id = uuid.uuid4()
    sid = uuid.uuid4()
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async with factory() as db:
        db.add(
            User(id=user_id, email=f"c2-{user_id}@arena.local", handle=f"c2{str(user_id)[:6]}")
        )
        db.add(
            Mission(
                id="claim-test-mission-2",
                title="x",
                difficulty="beginner",
                category="testing",
                repo_pack="pack",
                initial_commit="abc",
                estimated_minutes=5,
                failure_mode="none",
                skills_tested=[],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=sid,
                user_id=user_id,
                mission_id="claim-test-mission-2",
                status="submitting",
            )
        )
        await db.commit()

    async with factory() as db:
        session = await db.get(SessionRow, sid)
        won = await _claim_for_submit(db, session)
        assert won is False
