"""Supervision-event emitter — writes to DB and publishes to Redis.

DB writes are synchronous (blocking the current coroutine) and always happen
inside the caller's transaction. Redis publishes are *deferred* by default:

* The event row is added to the session and ``flush()``-ed so any FK errors
  surface immediately.
* The Redis publish payload is appended to ``db.info["_pending_publishes"]``
  rather than fanned-out inline.
* The ``get_db`` dependency drains that list AFTER ``await db.commit()``, so
  subscribers never see an event whose producing transaction rolled back.

Callers that need synchronous publish — e.g. ad-hoc tooling outside a request
or the magic-link / banned-commands middleware that runs its own session —
can pass ``publish_after_commit=False`` to ``emit``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.supervision_event import SupervisionEvent
from app.observability import (
    event_payload_truncated_total,
    event_publish_failures_total,
)

# Module-level lazy redis client cache — created once on first use.
_redis_client: Any | None = None

# Key into ``AsyncSession.info`` where pending publishes are queued.
_PENDING_KEY = "_pending_publishes"

# Hard upper bound on a single event payload — applied at *emit* time so both
# the DB row and the Redis publish carry the same truncated form. The legacy
# subscribe-time guard in ``app.ws.events`` remains as defence-in-depth (it
# catches anything that bypasses the emitter), but in steady state the DB and
# WS now agree on payload contents which keeps the timeline and live stream
# consistent for the FE.
_MAX_EVENT_BYTES = 65_536  # 64 KiB

# Sentinel marker the FE uses to render a "payload truncated" pill instead of
# the usual rich content. Mirrored in the ws.events stub builder so a
# subscribe-time truncation produces structurally identical output.
_TRUNCATED_PAYLOAD_REASON = "payload_exceeds_max_event_bytes"


def _payload_size_bytes(payload: dict[str, Any]) -> int | None:
    """Return the JSON byte-size of ``payload`` or None on serialisation error.

    Uses the same ``default=str`` coercion as the publish path so what we
    measure here matches what hits Redis (and the DB column, which stores the
    same dict but as JSONB).
    """
    try:
        return len(json.dumps(payload, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return None


def _truncated_payload(event_type: str, original_size_bytes: int) -> dict[str, Any]:
    """Build the canonical "we dropped the body" payload."""
    return {
        "truncated": True,
        "original_size_bytes": original_size_bytes,
        "max_size_bytes": _MAX_EVENT_BYTES,
        "reason": _TRUNCATED_PAYLOAD_REASON,
        "event_type": event_type,
    }


def _reset_redis_cache() -> None:
    """Drop the cached Redis client so the next ``get_redis`` rebuilds it.

    Called from the publish/emit paths when we see a connection-level error so
    a transient Redis blip (rolling restart, brief network partition) doesn't
    pin a dead client forever — the very next call rebuilds and recovers.
    """
    global _redis_client  # noqa: PLW0603
    _redis_client = None


def _is_redis_transport_error(exc: BaseException) -> bool:
    """True when ``exc`` is a Redis connection/timeout error worth resetting on.

    Imported lazily so the runtime doesn't pull ``redis`` at module import.
    """
    try:
        from redis import exceptions as redis_exc
    except Exception:  # pragma: no cover — redis missing entirely
        return False
    return isinstance(exc, (redis_exc.ConnectionError, redis_exc.TimeoutError))


async def get_redis() -> Any | None:
    """Return a lazily-created async Redis client, or None if unavailable."""
    global _redis_client  # noqa: PLW0603
    if _redis_client is not None:
        return _redis_client

    settings = get_settings()
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        # Cheap connectivity test — does not block long.
        # redis-py's ping() can return Awaitable[bool] or bool depending on
        # configuration; handle both.
        ping_result = client.ping()
        if asyncio.iscoroutine(ping_result):
            await ping_result
        _redis_client = client
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable ({}), event publish disabled", exc)
        return None


async def close_redis() -> None:
    """Close + drop the cached Redis client.

    Wired into the FastAPI lifespan shutdown so the API process tears the
    connection down cleanly instead of leaving a dangling socket.
    """
    global _redis_client  # noqa: PLW0603
    client = _redis_client
    _redis_client = None
    if client is None:
        return
    try:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            await aclose()
        else:  # pragma: no cover — older redis-py releases
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
    except Exception as exc:
        logger.debug("redis client close failed (ignored): {}", exc)


async def _publish(redis: Any, channel: str, message: str) -> bool:
    """Publish ``message`` to ``channel``. Returns True on success."""
    try:
        await redis.publish(channel, message)
        return True
    except Exception as exc:
        event_publish_failures_total.labels(reason="publish_error").inc()
        logger.warning("Redis publish failed for {}: {}", channel, exc)
        # Connection-level failures must invalidate the cached client so the
        # next call rebuilds against a (presumably-recovered) Redis.
        if _is_redis_transport_error(exc):
            _reset_redis_cache()
        return False


async def drain_pending_publishes(db: AsyncSession) -> None:
    """Publish every Redis message queued on ``db.info[_PENDING_KEY]``.

    Called by ``get_db`` AFTER ``await db.commit()`` so subscribers cannot
    observe an event whose producing transaction rolled back. The queue is
    cleared up-front so re-entrant calls cannot double-publish.

    When Redis is unavailable we log an ERROR (was: WARNING) and bump the
    publish-failure counter by the number of events dropped (P1-B5). Live
    subscribers will miss these events entirely — backfill will catch them up
    on next reconnect, but until then the FE timeline stays out of sync with
    the live stream. The structured log includes the session id and dropped
    count so an oncall pager can locate the affected sessions.
    """
    pending: list[tuple[str, str]] = db.info.get(_PENDING_KEY, [])
    if not pending:
        return
    db.info[_PENDING_KEY] = []

    redis = await get_redis()
    if redis is None:
        # Group dropped events by session so the log is readable even when a
        # single drain spans events from multiple sessions (unusual but
        # possible — e.g. an admin tool that touches several sessions in one
        # transaction).
        session_counts: dict[str, int] = {}
        for channel, _msg in pending:
            # Channel format: ``events:session:{uuid}``.
            sid = channel.rsplit(":", 1)[-1]
            session_counts[sid] = session_counts.get(sid, 0) + 1
        for sid, n in session_counts.items():
            logger.error(
                "[events] redis unavailable — {} events dropped for session={}; "
                "live subscribers will miss them until next reconnect+backfill",
                n,
                sid,
            )
        event_publish_failures_total.labels(reason="no_redis").inc(len(pending))
        return

    for channel, message in pending:
        await _publish(redis, channel, message)


def clear_pending_publishes(db: AsyncSession) -> None:
    """Discard any queued publishes (called from ``get_db`` on rollback)."""
    db.info[_PENDING_KEY] = []


class EventEmitter:
    """Insert supervision events to the DB and publish to Redis."""

    def __init__(
        self,
        db: AsyncSession,
        redis_client: Any | None = None,
    ):
        self.db = db
        self.redis = redis_client

    async def emit(
        self,
        session_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        *,
        publish_after_commit: bool = True,
    ) -> None:
        """Persist the event and queue (or fan-out) the Redis publish.

        When ``publish_after_commit=True`` (the default), the publish is
        deferred until ``drain_pending_publishes`` is invoked by ``get_db``.
        Callers running outside a request-scoped transaction (e.g. CLI or
        middleware that manages its own session) can set this to False for
        immediate fan-out.
        """
        now = datetime.now(UTC)

        # Truncate at emit time so the DB row and the Redis publish carry the
        # *same* payload — the FE's timeline (DB-backed) and live stream
        # (WS-backed) must agree on what a given event looks like, otherwise a
        # reconnect can show a fully populated payload on the timeline that
        # disagreed with the truncated stub the WS subscriber received
        # earlier in the session (P1-B3).
        size = _payload_size_bytes(payload)
        if size is not None and size > _MAX_EVENT_BYTES:
            logger.warning(
                "[events] truncating {} payload (size={}B > max={}B) — "
                "DB row + publish will both carry the stub form",
                event_type,
                size,
                _MAX_EVENT_BYTES,
            )
            event_payload_truncated_total.labels(event_type=event_type).inc()
            payload = _truncated_payload(event_type, original_size_bytes=size)

        event = SupervisionEvent(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            occurred_at=now,
        )
        self.db.add(event)
        await self.db.flush()  # get the auto-incremented id

        channel = f"events:session:{session_id}"
        # ``default=str`` coerces datetimes, Paths, UUIDs and any other
        # stringifiable object so an upstream caller can't silently break
        # the publish path just by passing a richer payload. On the rare
        # raise (a custom __repr__ that re-throws, for example) we keep
        # the DB row but drop the publish — subscribers will catch up via
        # the next reconnect's backfill.
        try:
            message = json.dumps(
                {
                    "id": event.id,
                    "session_id": str(session_id),
                    "event_type": event_type,
                    "payload": payload,
                    "occurred_at": now.isoformat(),
                },
                default=str,
            )
        except TypeError as exc:
            logger.warning(
                "supervision event {} type={} could not be serialised — DB row kept, publish dropped: {}",
                event.id,
                event_type,
                exc,
            )
            event_publish_failures_total.labels(reason="serialisation_error").inc()
            return

        if publish_after_commit:
            queue: list[tuple[str, str]] = self.db.info.setdefault(_PENDING_KEY, [])
            queue.append((channel, message))
            return

        # Immediate-publish path — used by callers that manage their own DB
        # session (e.g. middleware that commits inline).
        redis = self.redis
        if redis is None:
            try:
                redis = await get_redis()
            except Exception:
                redis = None
        if redis is not None:
            await _publish(redis, channel, message)
        else:
            event_publish_failures_total.labels(reason="no_redis").inc()
