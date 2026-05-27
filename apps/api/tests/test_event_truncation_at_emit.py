"""Emit-time payload truncation (P1-B3).

Truncation used to happen only at WS subscribe time, which meant the DB row
kept the full payload while the live stream got a stub. Reconnecting forced
a refetch from the DB and the FE saw two different versions of the same
event. We now truncate at emit time so the persisted row, the queued
publish, and any future replay all carry identical bytes.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.observability import event_payload_truncated_total
from app.sessions.events import (
    _MAX_EVENT_BYTES,
    _PENDING_KEY,
    _TRUNCATED_PAYLOAD_REASON,
    EventEmitter,
)


async def _seed_session(factory) -> uuid.UUID:
    async with factory() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"trunc-{uuid.uuid4().hex[:8]}@arena.local",
            display_name="Trunc",
        )
        mission_id = f"trunc-mission-{uuid.uuid4().hex[:6]}"
        db.add(user)
        db.add(
            Mission(
                id=mission_id,
                title="Trunc",
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
                expected_weak_dim="safety",
            )
        )
        sid = uuid.uuid4()
        db.add(
            SessionRow(
                id=sid,
                user_id=user.id,
                mission_id=mission_id,
                status="active",
            )
        )
        await db.commit()
        return sid


def _metric_value(event_type: str) -> float:
    """Snapshot the ``event_payload_truncated_total{event_type=…}`` counter."""
    sample = event_payload_truncated_total.labels(event_type=event_type)
    # prometheus_client doesn't expose ``.get()`` — peek at the internal _value.
    return float(sample._value.get())


@pytest.mark.asyncio
async def test_oversize_payload_is_truncated_in_db_and_publish(db_engine) -> None:
    """The persisted row AND the queued publish should both be the stub form."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    sid = await _seed_session(factory)

    # Build a payload comfortably over the 64 KiB budget.
    huge_blob = "x" * (_MAX_EVENT_BYTES + 4096)
    payload: dict[str, Any] = {"log": huge_blob, "marker": "should-be-dropped"}

    before = _metric_value("agent.responded")

    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        await emitter.emit(
            session_id=sid,
            event_type="agent.responded",
            payload=payload,
        )
        # Snapshot the pending publish *before* commit so we can compare the
        # message string against the DB row.
        pending = db.info.get(_PENDING_KEY, [])
        assert len(pending) == 1
        channel, message = pending[0]
        await db.commit()

    after = _metric_value("agent.responded")
    assert after == before + 1, "truncation metric should have advanced"

    # DB row: payload must be the stub form, not the original blob.
    async with factory() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.event_type == "agent.responded",
                    SupervisionEvent.session_id == sid,
                )
            )
        ).scalar_one()
        assert row.payload["truncated"] is True
        assert row.payload["reason"] == _TRUNCATED_PAYLOAD_REASON
        assert row.payload["original_size_bytes"] > _MAX_EVENT_BYTES
        # Original blob must be gone.
        assert "log" not in row.payload
        assert "marker" not in row.payload

    # Publish payload (the string queued for Redis) carries the same stub.
    parsed = json.loads(message)
    assert parsed["event_type"] == "agent.responded"
    assert parsed["payload"]["truncated"] is True
    assert parsed["payload"]["original_size_bytes"] == row.payload["original_size_bytes"]
    # Channel target is unchanged so subscribers still receive on the same key.
    assert channel == f"events:session:{sid}"


@pytest.mark.asyncio
async def test_in_budget_payload_passes_through_untouched(db_engine) -> None:
    """A normal-sized payload must not trip the truncation path."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    sid = await _seed_session(factory)

    payload = {"path": "src/x.ts", "added": 12, "removed": 4}
    before = _metric_value("file.edited")

    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        await emitter.emit(
            session_id=sid,
            event_type="file.edited",
            payload=payload,
        )
        await db.commit()

    after = _metric_value("file.edited")
    assert after == before, "no truncation should fire for an in-budget payload"

    async with factory() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.event_type == "file.edited",
                    SupervisionEvent.session_id == sid,
                )
            )
        ).scalar_one()
        assert row.payload == payload
