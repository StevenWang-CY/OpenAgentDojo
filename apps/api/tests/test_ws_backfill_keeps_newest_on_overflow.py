"""When the gap between the FE's ``last_id`` and the present exceeds
``_BACKFILL_LIMIT``, the backfill must keep the **newest** events. The
live subscription only resumes from the moment the WS subscribes, so any
event later than the backfill's tail that pre-dates the resume point
would be lost forever. The previous ``ORDER BY occurred_at ASC LIMIT N``
silently returned the *oldest* N and dropped the newest events in the
gap.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User


@pytest_asyncio.fixture
async def _bound_session_factory(db_engine):
    from app.db import session as session_module
    from app.ws import events as ws_events

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    original_db = session_module.AsyncSessionLocal
    original_ws = ws_events.AsyncSessionLocal
    session_module.AsyncSessionLocal = factory  # type: ignore[assignment]
    ws_events.AsyncSessionLocal = factory  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        session_module.AsyncSessionLocal = original_db  # type: ignore[assignment]
        ws_events.AsyncSessionLocal = original_ws  # type: ignore[assignment]


async def _seed_session(factory) -> uuid.UUID:
    async with factory() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"overflow-{uuid.uuid4().hex[:8]}@arena.local",
            display_name="Overflow",
        )
        db.add(user)
        db.add(
            Mission(
                id="ws-overflow-mission",
                title="WS Overflow",
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
        sid = uuid.uuid4()
        db.add(
            SessionRow(
                id=sid,
                user_id=user.id,
                mission_id="ws-overflow-mission",
                status="active",
            )
        )
        await db.commit()
        return sid


@pytest.mark.asyncio
async def test_backfill_keeps_newest_when_gap_exceeds_limit(
    _bound_session_factory, monkeypatch
) -> None:
    """Insert N > limit events and verify the newest are returned (not the oldest)."""
    from app.ws import events as ws_events
    from app.ws.events import _backfill

    # Use a small limit so the test is fast.
    monkeypatch.setattr(ws_events, "_BACKFILL_LIMIT", 5)

    sid = await _seed_session(_bound_session_factory)
    base = datetime.now(UTC)

    # 12 events, occurred_at strictly increasing.
    async with _bound_session_factory() as db:
        for i in range(12):
            db.add(
                SupervisionEvent(
                    session_id=sid,
                    event_type="prompt.submitted",
                    payload={"i": i},
                    occurred_at=base + timedelta(seconds=i),
                )
            )
        await db.commit()

    rows = await _backfill(sid, last_id=0)
    indexes = [r["payload"]["i"] for r in rows]
    # Must return the newest 5 in causal ascending order, NOT the oldest 5.
    assert indexes == [7, 8, 9, 10, 11], indexes
