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
from app.observability import event_publish_failures_total

# Module-level lazy redis client cache — created once on first use.
_redis_client: Any | None = None

# Key into ``AsyncSession.info`` where pending publishes are queued.
_PENDING_KEY = "_pending_publishes"


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


async def _publish(redis: Any, channel: str, message: str) -> bool:
    """Publish ``message`` to ``channel``. Returns True on success."""
    try:
        await redis.publish(channel, message)
        return True
    except Exception as exc:
        event_publish_failures_total.labels(reason="publish_error").inc()
        logger.warning("Redis publish failed for {}: {}", channel, exc)
        return False


async def drain_pending_publishes(db: AsyncSession) -> None:
    """Publish every Redis message queued on ``db.info[_PENDING_KEY]``.

    Called by ``get_db`` AFTER ``await db.commit()`` so subscribers cannot
    observe an event whose producing transaction rolled back. The queue is
    cleared up-front so re-entrant calls cannot double-publish.
    """
    pending: list[tuple[str, str]] = db.info.get(_PENDING_KEY, [])
    if not pending:
        return
    db.info[_PENDING_KEY] = []

    redis = await get_redis()
    if redis is None:
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

        event = SupervisionEvent(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            occurred_at=now,
        )
        self.db.add(event)
        await self.db.flush()  # get the auto-incremented id

        channel = f"events:session:{session_id}"
        message = json.dumps(
            {
                "id": event.id,
                "session_id": str(session_id),
                "event_type": event_type,
                "payload": payload,
                "occurred_at": now.isoformat(),
            }
        )

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
