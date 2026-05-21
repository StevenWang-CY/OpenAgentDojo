"""WS events stream uses Redis pub/sub for live fanout.

We stub :func:`app.sessions.events.get_redis` to return an in-process fake
that mimics the small subset of the ``redis.asyncio`` API we touch (publish,
pubsub, subscribe, listen, unsubscribe, aclose). The WS subscriber should
forward published messages to the client and close gracefully on a graded
event.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest


class _FakePubSub:
    def __init__(self, broker: "_FakeRedis") -> None:  # noqa: UP037 — forward ref
        self.broker = broker
        self.subscribed: set[str] = set()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, *channels: str) -> None:
        for ch in channels:
            self.subscribed.add(ch)
            self.broker._subs.setdefault(ch, []).append(self._queue)

    async def unsubscribe(self, *channels: str) -> None:
        for ch in channels:
            self.subscribed.discard(ch)
            self.broker._subs.get(ch, []).remove(
                self._queue
            ) if self._queue in self.broker._subs.get(ch, []) else None

    async def listen(self):
        while True:
            msg = await self._queue.get()
            yield msg

    async def aclose(self) -> None:
        return None


class _FakeRedis:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def publish(self, channel: str, data: str) -> int:
        listeners = self._subs.get(channel, [])
        for q in listeners:
            await q.put({"type": "message", "channel": channel, "data": data})
        return len(listeners)

    def pubsub(self, ignore_subscribe_messages: bool = False) -> _FakePubSub:
        return _FakePubSub(self)


class _FakeWebSocket:
    """The bare minimum of starlette.WebSocket the events handler touches."""

    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_ws_events_subscribes_to_redis_and_closes_on_graded(monkeypatch) -> None:
    from app.ws import events as ws_events

    broker = _FakeRedis()
    sid = uuid.uuid4()

    # Stub the lazy redis accessor + DB backfill (no DB in this unit test).
    async def _fake_get_redis():
        return broker

    async def _fake_backfill(_sid, _last):
        return []

    monkeypatch.setattr(ws_events, "get_redis", _fake_get_redis)
    monkeypatch.setattr(ws_events, "_backfill", _fake_backfill)

    # Stub auth so we don't need a real signing secret in this micro-test.
    monkeypatch.setattr(ws_events, "verify_ws_token", lambda *_, **__: True)

    ws = _FakeWebSocket()
    handler = asyncio.create_task(ws_events.events_ws(ws, sid, token="ignored", last_id=0))

    # Let the handler subscribe.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if any(
            f"events:session:{sid}" in subs.subscribed
            for subs_list in broker._subs.values()
            for subs in []
        ):
            break

    # The handler created its own pubsub queue inside the broker.
    channel = f"events:session:{sid}"
    # Spin until at least one listener registered.
    for _ in range(50):
        if broker._subs.get(channel):
            break
        await asyncio.sleep(0.01)
    assert broker._subs.get(channel), "WS handler did not subscribe to the redis channel"

    # Publish a regular event → it should land on the WS.
    await broker.publish(
        channel,
        json.dumps(
            {
                "id": 1,
                "session_id": str(sid),
                "event_type": "agent.responded",
                "payload": {"turn": 1},
                "occurred_at": "2026-05-21T00:00:00+00:00",
            }
        ),
    )
    for _ in range(50):
        if ws.sent:
            break
        await asyncio.sleep(0.01)
    assert ws.sent, "expected the published event to be forwarded over the WS"
    assert ws.sent[0]["event_type"] == "agent.responded"

    # Publish a graded event → handler should close gracefully (1000, 'graded').
    await broker.publish(
        channel,
        json.dumps(
            {
                "id": 2,
                "session_id": str(sid),
                "event_type": "submission.graded",
                "payload": {"score": 87},
                "occurred_at": "2026-05-21T00:01:00+00:00",
            }
        ),
    )

    await asyncio.wait_for(handler, timeout=2.0)
    assert ws.closed is not None
    assert ws.closed[0] == 1000
    assert ws.closed[1] == "graded"


@pytest.mark.asyncio
async def test_event_emitter_publishes_to_session_channel(monkeypatch) -> None:
    """Confirm the publisher side targets the same channel the WS subscribes to."""
    from app.sessions.events import EventEmitter

    broker = _FakeRedis()

    # A fake db session — just enough to swallow .add and .flush. The .info
    # dict matches SQLAlchemy's Session.info shape so EventEmitter's deferred
    # publish path can queue against it.
    class _DB:
        def __init__(self) -> None:
            self.info: dict[str, list[tuple[str, str]]] = {}

        def add(self, _row):
            return None

        async def flush(self):
            # Stamp an id so the emitter's payload is well-formed.
            return None

    emitter = EventEmitter(db=_DB(), redis_client=broker)

    # Pre-subscribe so the publish lands somewhere observable.
    sid = uuid.uuid4()
    channel = f"events:session:{sid}"
    ps = broker.pubsub()
    await ps.subscribe(channel)

    # Force immediate publish so the test asserts fan-out rather than the
    # deferred-until-commit queue path.
    await emitter.emit(sid, "user.prompted", {"text": "hi"}, publish_after_commit=False)

    msg = await asyncio.wait_for(ps._queue.get(), timeout=1.0)
    parsed = json.loads(msg["data"])
    assert parsed["event_type"] == "user.prompted"
    assert parsed["session_id"] == str(sid)
