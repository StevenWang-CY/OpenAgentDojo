"""Circuit-break behaviour for ``drain_pending_publishes`` (P1-6).

A wedged Redis (or one that's been partitioned off the cluster) used to
make the drain loop retry every queued publish against the same broken
publisher, burning the request handler's exit budget on socket
connect/write/timeout cycles. P1-6 adds a circuit-breaker: after a run
of consecutive failures the loop bails out and counts the *remaining*
events against ``event_publish_failures_total{reason="circuit_broken"}``
so the drop is visible on the dashboard.

This test seeds a queue of 10 publishes plus a publisher that always
fails, then asserts:

* the publisher was attempted at most ``threshold + 1`` times
  (the failures-in-a-row that trip the breaker);
* the metric advanced by the count of UNATTEMPTED publishes;
* the queue is empty when the drain returns (the contract is
  one-shot per commit; preserving the queue would risk
  double-publishing on the next request).
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
            email=f"circuit-{uuid.uuid4().hex[:8]}@arena.local",
            display_name="Circuit",
        )
        mission_id = f"circuit-mission-{uuid.uuid4().hex[:6]}"
        db.add(user)
        db.add(
            Mission(
                id=mission_id,
                title="Circuit",
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


def _circuit_broken_metric_value() -> float:
    sample = event_publish_failures_total.labels(reason="circuit_broken")
    return float(sample._value.get())


@pytest.mark.asyncio
async def test_drain_circuit_breaks_after_consecutive_failures(db_engine, monkeypatch) -> None:
    """A publisher that always fails trips the breaker mid-queue."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    sid = await _seed_session(factory)

    # Force ``get_redis`` to return a stub object so the drain enters the
    # publish loop rather than the no-redis short-circuit. The stub's
    # ``publish`` call is wired through ``_publish`` below.
    class _FakeRedis:
        pass

    fake_redis = _FakeRedis()

    from app.sessions import events as events_module

    async def _get_redis_stub():
        return fake_redis

    monkeypatch.setattr(events_module, "get_redis", _get_redis_stub)

    publish_attempts = 0

    async def _always_fail(redis_client, channel, message):  # signature mirrors _publish
        nonlocal publish_attempts
        publish_attempts += 1
        return False  # _publish returns bool; False = failure

    monkeypatch.setattr(events_module, "_publish", _always_fail)

    before = _circuit_broken_metric_value()

    # Queue 10 events on the session.
    queued = 10
    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        for i in range(queued):
            await emitter.emit(
                session_id=sid,
                event_type="prompt.submitted",
                payload={"turn_index": i, "text": f"msg-{i}"},
            )
        await db.commit()
        await drain_pending_publishes(db)
        # Queue is drained one-shot per commit; the breaker still
        # empties it.
        assert db.info.get("_pending_publishes", []) == []

    after = _circuit_broken_metric_value()
    threshold = events_module._DRAIN_FAILURE_THRESHOLD

    # The breaker fires AFTER ``threshold + 1`` consecutive failures
    # (failures variable is incremented before the > comparison).
    assert publish_attempts == threshold + 1, (
        f"expected publisher to be called exactly {threshold + 1} times "
        f"before the breaker tripped, got {publish_attempts}"
    )
    expected_dropped = queued - publish_attempts
    assert after - before == expected_dropped, (
        f"circuit_broken counter should advance by {expected_dropped} "
        f"(remaining undrained events); before={before} after={after}"
    )
