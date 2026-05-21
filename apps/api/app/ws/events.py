"""Server-push supervision-events stream.

On connect we backfill from the DB (events with id > ``last_id``) and then
switch to a Redis subscription on ``events:session:{session_id}`` for live
fanout. ``EventEmitter.emit`` writes to the same channel.

Graceful close on ``submission.graded`` lets the client tear down its socket
in one round trip rather than waiting for a heartbeat to lapse.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from loguru import logger
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.supervision_event import SupervisionEvent
from app.sessions.events import get_redis
from app.ws.auth import verify_ws_token

router = APIRouter(tags=["ws"])

# If we fall this far behind on the subscriber, drop the WS so the client can
# reconnect cleanly rather than dribble stale data.
_BACKPRESSURE_LIMIT = 100

# Graded sessions terminate the stream with a normal close so the frontend
# can navigate to the report page without retrying.
_GRADED_CLOSE_CODE = status.WS_1000_NORMAL_CLOSURE
_GRADED_CLOSE_REASON = "graded"

# Fallback poll cadence used only when Redis is unavailable on connect.
_FALLBACK_POLL_INTERVAL_S = 1.0


def _serialise(ev: SupervisionEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "session_id": str(ev.session_id),
        "event_type": ev.event_type,
        "payload": ev.payload,
        "occurred_at": ev.occurred_at.isoformat(),
    }


async def _backfill(session_id: uuid.UUID, last_id: int) -> list[dict[str, Any]]:
    """Pull persisted events strictly newer than ``last_id`` from the DB."""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(SupervisionEvent)
            .where(SupervisionEvent.session_id == session_id)
            .where(SupervisionEvent.id > last_id)
            .order_by(SupervisionEvent.id)
        )
        result = await db.execute(stmt)
        return [_serialise(ev) for ev in result.scalars().all()]


def _is_graded(message: dict[str, Any]) -> bool:
    return message.get("event_type") == "submission.graded"


async def _send_backfill(
    websocket: WebSocket, session_id: uuid.UUID, cursor: int
) -> tuple[int, bool]:
    """Push every persisted event newer than ``cursor`` and return the new cursor.

    The boolean is True if a ``submission.graded`` event was sent (caller
    should close).
    """
    try:
        backfill = await _backfill(session_id, cursor)
    except Exception as exc:
        logger.warning("events backfill failed for {}: {}", session_id, exc)
        return cursor, False

    saw_graded = False
    for ev in backfill:
        cursor = max(cursor, int(ev["id"]))
        await websocket.send_json(ev)
        if _is_graded(ev):
            saw_graded = True
    return cursor, saw_graded


async def _pubsub_drain(pubsub, queue: asyncio.Queue, overflow: asyncio.Event) -> None:
    """Read Redis pubsub messages onto ``queue``; flag ``overflow`` if full."""
    try:
        async for raw in pubsub.listen():
            if raw is None or raw.get("type") != "message":
                continue
            data = raw.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(data)
            except (TypeError, ValueError):
                continue
            try:
                queue.put_nowait(parsed)
            except asyncio.QueueFull:
                overflow.set()
                return
    except asyncio.CancelledError:  # pragma: no cover — shutdown path
        raise
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("redis pubsub reader stopped: {}", exc)


async def _forward_subscription(
    websocket: WebSocket,
    session_id: uuid.UUID,
    cursor: int,
    pubsub,
    channel: str,
) -> None:
    """Pump from the pubsub queue out to the WS until graded / disconnect."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_BACKPRESSURE_LIMIT)
    overflow = asyncio.Event()
    reader_task = asyncio.create_task(
        _pubsub_drain(pubsub, queue, overflow), name="events-pubsub-reader"
    )
    try:
        while True:
            get_task = asyncio.create_task(queue.get())
            overflow_task = asyncio.create_task(overflow.wait())
            done, pending = await asyncio.wait(
                {get_task, overflow_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
            if overflow_task in done and overflow.is_set():
                logger.warning(
                    "events ws falling behind for {} — closing for reconnect",
                    session_id,
                )
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="backpressure")
                return

            message = get_task.result()
            try:
                msg_id = int(message.get("id", 0))
            except (TypeError, ValueError):
                msg_id = 0
            # De-dupe in case the backfill already covered this id.
            if msg_id and msg_id <= cursor:
                continue
            cursor = max(cursor, msg_id)

            await websocket.send_json(message)

            if _is_graded(message):
                await websocket.close(code=_GRADED_CLOSE_CODE, reason=_GRADED_CLOSE_REASON)
                return
    except WebSocketDisconnect:
        return
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await pubsub.unsubscribe(channel)
        except Exception:  # pragma: no cover
            pass
        try:
            await pubsub.aclose()
        except Exception:  # pragma: no cover
            pass


@router.websocket("/ws/sessions/{session_id}/events")
async def events_ws(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(""),
    last_id: int = Query(0, ge=0),
):
    sid_str = str(session_id)
    if not verify_ws_token(token, sid_str):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="bad token")
        return

    await websocket.accept()

    cursor, saw_graded = await _send_backfill(websocket, session_id, last_id)
    if saw_graded:
        await websocket.close(code=_GRADED_CLOSE_CODE, reason=_GRADED_CLOSE_REASON)
        return

    redis = await get_redis()
    if redis is None:
        await _poll_loop(websocket, session_id, cursor)
        return

    channel = f"events:session:{session_id}"
    pubsub = redis.pubsub(ignore_subscribe_messages=True)
    try:
        await pubsub.subscribe(channel)
    except Exception as exc:
        logger.warning("redis subscribe failed for {}: {}", channel, exc)
        await _poll_loop(websocket, session_id, cursor)
        return

    await _forward_subscription(websocket, session_id, cursor, pubsub, channel)


async def _poll_loop(websocket: WebSocket, session_id: uuid.UUID, start_cursor: int) -> None:
    """Last-resort polling path used when Redis is not configured."""
    cursor = start_cursor
    try:
        while True:
            try:
                rows = await _backfill(session_id, cursor)
            except Exception as exc:
                logger.debug("events poll error: {}", exc)
                rows = []
            for ev in rows:
                cursor = max(cursor, int(ev["id"]))
                await websocket.send_json(ev)
                if _is_graded(ev):
                    await websocket.close(code=_GRADED_CLOSE_CODE, reason=_GRADED_CLOSE_REASON)
                    return
            await asyncio.sleep(_FALLBACK_POLL_INTERVAL_S)
    except WebSocketDisconnect:
        return
