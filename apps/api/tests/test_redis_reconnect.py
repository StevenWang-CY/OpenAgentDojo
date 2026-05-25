"""Cached Redis client must be cleared on a transport error.

A flaky Redis (rolling restart, brief network blip) used to pin a dead
client forever — the cached connection raised ``ConnectionError`` on every
subsequent publish and we never recovered until the API restarted. The
emitter now drops the cached client when the publish path sees a Redis
transport error so the next ``get_redis`` rebuilds against a presumably-
recovered Redis.
"""

from __future__ import annotations

import pytest

import app.sessions.events as events_module


class _RaisingClient:
    """Fake Redis client whose ``publish`` raises a transport error."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    async def publish(self, _channel: str, _message: str) -> int:
        self.calls += 1
        raise self._exc


@pytest.mark.asyncio
async def test_publish_clears_cached_client_on_connection_error() -> None:
    redis_exc = pytest.importorskip("redis.exceptions")

    # Seed the cache with a client that will raise ConnectionError on publish.
    boom = redis_exc.ConnectionError("connection refused")
    events_module._redis_client = _RaisingClient(boom)
    try:
        ok = await events_module._publish(events_module._redis_client, "events:session:x", "{}")
        assert ok is False
        # The cached client MUST have been cleared so the next get_redis call
        # rebuilds against the recovered Redis (or re-fails cleanly with its
        # own warning).
        assert events_module._redis_client is None
    finally:
        events_module._redis_client = None


@pytest.mark.asyncio
async def test_publish_clears_cached_client_on_timeout_error() -> None:
    redis_exc = pytest.importorskip("redis.exceptions")

    events_module._redis_client = _RaisingClient(redis_exc.TimeoutError("slow"))
    try:
        ok = await events_module._publish(events_module._redis_client, "events:session:x", "{}")
        assert ok is False
        assert events_module._redis_client is None
    finally:
        events_module._redis_client = None


@pytest.mark.asyncio
async def test_publish_keeps_cached_client_on_unrelated_error() -> None:
    """Non-transport errors (e.g. ValueError from a custom serializer) must
    NOT invalidate the cached client — that would amplify a benign bug into
    a full reconnect storm."""
    events_module._redis_client = _RaisingClient(ValueError("bad payload"))
    try:
        ok = await events_module._publish(events_module._redis_client, "events:session:x", "{}")
        assert ok is False
        # Cache survives.
        assert events_module._redis_client is not None
    finally:
        events_module._redis_client = None


@pytest.mark.asyncio
async def test_close_redis_resets_cache() -> None:
    """``close_redis()`` (lifespan shutdown hook) aclose()s and clears cache."""

    class _AcloseClient:
        def __init__(self) -> None:
            self.closed: bool = False

        async def aclose(self) -> None:
            self.closed = True

    client = _AcloseClient()
    events_module._redis_client = client
    try:
        await events_module.close_redis()
        assert client.closed is True
        assert events_module._redis_client is None
    finally:
        events_module._redis_client = None
