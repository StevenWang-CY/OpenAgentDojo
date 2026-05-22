"""WS backfill order: ``(occurred_at, id)`` not just ``id`` (P0-B1).

If two API workers insert supervision events with mildly skewed clocks (or a
worker rewrites an event's ``occurred_at`` to an earlier point in time during
replay), insertion order can disagree with causal order. The WS backfill MUST
deliver events in the same order the grading engine and timeline endpoint
read them — ``occurred_at`` ascending, with ``id`` as a stable tie-breaker.

Without this ordering the frontend's "play forward" assumption breaks: a
causally-earlier event would arrive after a later one and the cursor logic
would silently drop it (``msg_id <= cursor``).
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
    """Bind both the db.session module *and* the ws.events imported alias to the test engine.

    ``ws.events._backfill`` captured ``AsyncSessionLocal`` at import time via
    ``from app.db.session import AsyncSessionLocal`` — that's a separate
    binding from the module attribute, so a fixture that only rewrites
    ``app.db.session.AsyncSessionLocal`` would leave the WS module pointing
    at the original (un-tabled) sessionmaker.
    """
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
            email=f"ordering-{uuid.uuid4().hex[:8]}@arena.local",
            display_name="Ordering",
        )
        db.add(user)
        db.add(
            Mission(
                id="ws-order-mission",
                title="WS Order",
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
                mission_id="ws-order-mission",
                status="active",
            )
        )
        await db.commit()
        return sid


@pytest.mark.asyncio
async def test_backfill_orders_by_occurred_at_then_id(_bound_session_factory) -> None:
    """Inserting events whose ``occurred_at`` is *reversed* vs. insertion id
    should yield them ordered by ``(occurred_at, id)`` — not by id alone."""
    from app.ws.events import _backfill

    sid = await _seed_session(_bound_session_factory)
    base = datetime.now(UTC)

    # Insert three rows where insertion order ≠ occurred_at order.
    # Insert order (and thus auto-increment id): A (t=+30s), B (t=+0s), C (t=+15s).
    # Expected backfill order by (occurred_at, id): B (0s), C (15s), A (30s).
    async with _bound_session_factory() as db:
        ev_a = SupervisionEvent(
            session_id=sid,
            event_type="prompt.submitted",
            payload={"label": "A"},
            occurred_at=base + timedelta(seconds=30),
        )
        ev_b = SupervisionEvent(
            session_id=sid,
            event_type="prompt.submitted",
            payload={"label": "B"},
            occurred_at=base,
        )
        ev_c = SupervisionEvent(
            session_id=sid,
            event_type="prompt.submitted",
            payload={"label": "C"},
            occurred_at=base + timedelta(seconds=15),
        )
        db.add(ev_a)
        await db.flush()
        db.add(ev_b)
        await db.flush()
        db.add(ev_c)
        await db.commit()
        # Sanity: id order is A < B < C (insertion order).
        assert ev_a.id < ev_b.id < ev_c.id

    rows = await _backfill(sid, last_id=0)
    labels = [r["payload"]["label"] for r in rows]
    assert labels == ["B", "C", "A"], f"expected occurred_at order [B,C,A], got {labels}"


@pytest.mark.asyncio
async def test_backfill_id_tiebreaks_identical_occurred_at(
    _bound_session_factory,
) -> None:
    """Two events with identical ``occurred_at`` should sort by id (insertion)."""
    from app.ws.events import _backfill

    sid = await _seed_session(_bound_session_factory)
    t = datetime.now(UTC)

    async with _bound_session_factory() as db:
        first = SupervisionEvent(
            session_id=sid,
            event_type="prompt.submitted",
            payload={"label": "first"},
            occurred_at=t,
        )
        second = SupervisionEvent(
            session_id=sid,
            event_type="prompt.submitted",
            payload={"label": "second"},
            occurred_at=t,
        )
        db.add(first)
        await db.flush()
        db.add(second)
        await db.commit()
        assert first.id < second.id

    rows = await _backfill(sid, last_id=0)
    labels = [r["payload"]["label"] for r in rows]
    assert labels == ["first", "second"]
