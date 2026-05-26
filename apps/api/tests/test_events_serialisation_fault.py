"""Publish-step serialisation fault tolerance for the emit path.

``EventEmitter.emit`` persists the event row first, then builds a separate
publish payload via ``json.dumps(..., default=str)``. The previous catch was
narrow (``except TypeError``) — if a custom ``__str__`` raises
``AttributeError`` or ``ValueError`` during ``default=str`` coercion, the
exception escaped and 500'd the entire request handler, leaving the session
pinned mid-submit.

The DB row write uses the same coercion (so in practice it would fail first),
but the contract for the publish step is independent: a publish-time
serialisation error must not propagate up to the route handler. The widened
``except (TypeError, ValueError, AttributeError)`` matches the canonical set
of exceptions ``json.dumps(default=str)`` can raise.

We exercise this by monkeypatching ``json.dumps`` inside the events module so
the DB write (which uses SQLAlchemy's own encoder) succeeds normally and the
emit-time publish call raises — same shape a real divergence between the two
encoders would produce.
"""

from __future__ import annotations

import json as _stdlib_json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.observability import event_publish_failures_total
from app.sessions.events import EventEmitter


async def _seed_session(factory) -> uuid.UUID:
    async with factory() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"badstr-{uuid.uuid4().hex[:8]}@arena.local",
            display_name="BadStr",
        )
        mission_id = f"badstr-mission-{uuid.uuid4().hex[:6]}"
        db.add(user)
        db.add(
            Mission(
                id=mission_id,
                title="BadStr",
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
    sample = event_publish_failures_total.labels(reason="serialisation_error")
    return float(sample._value.get())


def _make_targeted_dumps(target_session_id: uuid.UUID, exc: BaseException):
    """Return a ``json.dumps`` replacement that only blows up on the publish payload.

    The publish-time dict has a stable shape — ``{"id", "session_id",
    "event_type", "payload", "occurred_at"}``. We match on that and the target
    session id so unrelated json.dumps calls (notably size measurement and
    SQLAlchemy's own encoder, which doesn't even use this hook) pass through.
    """
    real_dumps = _stdlib_json.dumps

    def _dumps(obj: Any, *args: Any, **kwargs: Any) -> str:
        if (
            isinstance(obj, dict)
            and obj.get("session_id") == str(target_session_id)
            and {"id", "event_type", "payload", "occurred_at"} <= obj.keys()
        ):
            raise exc
        return real_dumps(obj, *args, **kwargs)

    return _dumps


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "publish_error",
    [
        AttributeError("simulated bad __str__"),
        ValueError("simulated bad __str__"),
        TypeError("simulated bad __str__"),
    ],
)
async def test_emit_publish_serialisation_failure_keeps_row(
    db_engine, monkeypatch, publish_error
) -> None:
    """A publish-time serialisation raise must not crash the caller.

    The narrow ``except TypeError`` previously let ``AttributeError`` and
    ``ValueError`` propagate. The broadened catch keeps the DB row, drops the
    publish, and increments the failure counter — same contract as the legacy
    ``TypeError`` path now applied uniformly to the canonical set of
    json.dumps exceptions.
    """
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    sid = await _seed_session(factory)

    before = _metric_value()

    # Target the publish-step json.dumps only. The DB write uses SQLAlchemy's
    # JSON column encoder, which doesn't route through this monkeypatched
    # symbol, so the row still flushes cleanly.
    from app.sessions import events as events_module

    monkeypatch.setattr(events_module.json, "dumps", _make_targeted_dumps(sid, publish_error))

    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        # The emit call must not raise — the contract is "row kept, publish dropped".
        await emitter.emit(
            session_id=sid,
            event_type="prompt.submitted",
            payload={"turn_index": 0, "text": "hello"},
        )
        await db.commit()

    after = _metric_value()
    assert after == before + 1, (
        f"emit must increment ``serialisation_error`` counter once per "
        f"unserialisable publish payload regardless of exception class; "
        f"before={before} after={after}, raised={type(publish_error).__name__}"
    )

    # DB row must still be there — emit's contract is "row kept, publish dropped".
    async with factory() as db:
        rows = list(
            (
                await db.execute(
                    select(SupervisionEvent).where(SupervisionEvent.session_id == sid)
                )
            ).scalars()
        )
        assert len(rows) == 1, (
            f"expected exactly one persisted event for {type(publish_error).__name__}, "
            f"got {len(rows)}"
        )
        assert rows[0].event_type == "prompt.submitted"
