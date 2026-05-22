"""Drain path when Redis is unavailable (P1-B5).

The deferred-publish path silently dropped events on the floor when
``get_redis`` returned None — only a single WARNING fired and the metric
didn't carry session context. On a partial Redis outage this left the
operator with no actionable signal. We now log ERROR with the session id and
dropped count, and increment ``event_publish_failures_total{reason="no_redis"}``
by the *number of events dropped* (not just one).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User
from app.observability import event_publish_failures_total
from app.sessions.events import EventEmitter, drain_pending_publishes


async def _seed_session(factory) -> uuid.UUID:
    async with factory() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"noredis-{uuid.uuid4().hex[:8]}@arena.local",
            display_name="NoRedis",
        )
        mission_id = f"noredis-mission-{uuid.uuid4().hex[:6]}"
        db.add(user)
        db.add(
            Mission(
                id=mission_id,
                title="NoRedis",
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
                mission_id=mission_id,
                status="active",
            )
        )
        await db.commit()
        return sid


def _metric_value() -> float:
    sample = event_publish_failures_total.labels(reason="no_redis")
    return float(sample._value.get())


@pytest.mark.asyncio
async def test_drain_with_no_redis_logs_error_and_bumps_counter(db_engine, monkeypatch) -> None:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    sid = await _seed_session(factory)

    # Force get_redis to return None so the drain hits the no_redis branch.
    from app.sessions import events as events_module

    async def _no_redis():
        return None

    monkeypatch.setattr(events_module, "get_redis", _no_redis)

    before = _metric_value()

    from loguru import logger as _logger

    error_lines: list[str] = []
    sink_id = _logger.add(lambda m: error_lines.append(str(m)), level="ERROR")

    try:
        async with factory() as db:
            emitter = EventEmitter(db=db, redis_client=None)
            await emitter.emit(
                session_id=sid,
                event_type="prompt.submitted",
                payload={"turn_index": 0, "text": "hello", "char_count": 5},
            )
            await emitter.emit(
                session_id=sid,
                event_type="agent.responded",
                payload={"turn_index": 0, "response_summary": "hi"},
            )
            # Commit (writes the DB rows) and drain (which trips the no-redis branch).
            await db.commit()
            await drain_pending_publishes(db)
    finally:
        _logger.remove(sink_id)

    after = _metric_value()
    assert after == before + 2, (
        f"counter should advance by exactly the number of dropped events; "
        f"before={before} after={after}"
    )

    # Exactly one ERROR for the session (n=2 events dropped).
    matching = [line for line in error_lines if "redis unavailable" in line and str(sid) in line]
    assert len(matching) == 1, error_lines
    assert "2 events dropped" in matching[0]
