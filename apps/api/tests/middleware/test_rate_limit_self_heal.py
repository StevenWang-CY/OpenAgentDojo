"""P1 — the rate-limiter self-heals when Redis recovers.

``_get_redis`` used to latch ``self._redis_probed = True`` on the first probe
and return early forever after. If Redis was unreachable on the first
rate-limited request, the middleware fell back to a per-worker in-memory
counter PERMANENTLY (until process restart) — even once Redis came back.
Under N workers that silently turns an ``M``/min cap into ``N×M``/min.

The fix mirrors ``app/sessions/events.py``: instead of a one-shot latch we
keep a ``_next_probe_at`` monotonic backoff. When the cached client is None we
re-probe once the backoff elapses, and a transport error on the hit path nulls
the cached client + arms the backoff so the next request re-establishes the
Redis-backed counter.

These tests drive the middleware's ``_get_redis``/``_count`` directly (no HTTP
stack) so they assert the recovery semantics precisely and deterministically.
"""

from __future__ import annotations

import asyncio

import pytest
from redis import exceptions as redis_exc

from app.middleware import rate_limit as rl
from app.middleware.rate_limit import RateLimitMiddleware


class _FakePipeline:
    """Minimal stand-in for a redis-py async pipeline."""

    def __init__(self, client: "_FakeRedis") -> None:
        self._client = client
        self._key: str | None = None

    def incr(self, key: str) -> None:
        self._key = key

    def expire(self, key: str, window_s: int) -> None:  # noqa: ARG002
        pass

    async def execute(self) -> list[int]:
        assert self._key is not None
        self._client.counts[self._key] = self._client.counts.get(self._key, 0) + 1
        return [self._client.counts[self._key]]


class _FakeRedis:
    """A reachable fake Redis whose pipeline increments an in-process dict."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ping_calls = 0

    async def ping(self) -> bool:
        self.ping_calls += 1
        return True

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)


def _make_app_stub():
    """A trivial ASGI app — the middleware never calls it in these tests."""

    async def _app(scope, receive, send):  # pragma: no cover — never invoked
        raise AssertionError("downstream app should not be reached")

    return _app


@pytest.mark.asyncio
async def test_reprobes_redis_after_backoff_once_reachable(monkeypatch) -> None:
    """First probe fails → memory fallback; after backoff Redis is used again.

    Pre-fix this FAILS: the one-shot ``_redis_probed`` latch pins the
    in-memory counter forever, so the later ``_count`` never touches the now
    reachable ``_FakeRedis`` and ``ping_calls`` stays at 0.
    """
    mw = RateLimitMiddleware(_make_app_stub())

    fake = _FakeRedis()
    state = {"reachable": False}

    def _fake_from_url(url, **kwargs):  # noqa: ARG001
        if not state["reachable"]:
            raise redis_exc.ConnectionError("redis down")
        return fake

    monkeypatch.setattr("redis.asyncio.from_url", _fake_from_url)

    # Drive a virtual monotonic clock so the test doesn't actually sleep.
    clock = {"t": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])

    # 1) Redis unreachable on the first probe → falls back to in-memory counter.
    first = await mw._count("ratelimit:test:ip:x:0", 60)
    assert first == 1
    assert mw._redis is None
    assert fake.ping_calls == 0  # never reached a live client

    # 2) Still inside the backoff window → must NOT re-probe yet (no churn).
    clock["t"] += rl._REDIS_REPROBE_BACKOFF_S / 2
    second = await mw._count("ratelimit:test:ip:x:0", 60)
    assert second == 2  # memory counter advanced
    assert fake.ping_calls == 0

    # 3) Redis recovers, and the backoff has elapsed → next request re-probes
    #    and uses the Redis-backed counter again.
    state["reachable"] = True
    clock["t"] += rl._REDIS_REPROBE_BACKOFF_S + 0.1
    third = await mw._count("ratelimit:test:ip:x:0", 60)
    assert mw._redis is fake, "middleware should have re-established the Redis client"
    assert fake.ping_calls == 1
    # Redis bucket starts fresh at 1 (it never saw the two memory-only hits).
    assert third == 1
    assert fake.counts["ratelimit:test:ip:x:0"] == 1


@pytest.mark.asyncio
async def test_hit_path_transport_error_clears_cached_client(monkeypatch) -> None:
    """A transport error in ``_count`` nulls the cached client + arms re-probe.

    Pre-fix the cached client survived (only the in-memory fallback was used
    for that one call) and, combined with the probe latch, recovery never
    happened. After the fix the dead client is dropped so the next probe can
    re-establish a healthy one.
    """
    mw = RateLimitMiddleware(_make_app_stub())

    clock = {"t": 5000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])

    class _FlakyRedis:
        def __init__(self) -> None:
            self.counts: dict[str, int] = {}

        async def ping(self) -> bool:
            return True

        def pipeline(self) -> "_FlakyPipeline":
            return _FlakyPipeline()

    class _FlakyPipeline:
        def incr(self, key: str) -> None:  # noqa: ARG002
            pass

        def expire(self, key: str, window_s: int) -> None:  # noqa: ARG002
            pass

        async def execute(self) -> list[int]:
            raise redis_exc.ConnectionError("connection reset mid-pipeline")

    flaky = _FlakyRedis()
    monkeypatch.setattr("redis.asyncio.from_url", lambda url, **kw: flaky)  # noqa: ARG005

    # First hit: probe succeeds (client cached), but the pipeline execute
    # raises a transport error → memory fallback for this call AND the cached
    # client is dropped so we don't pin a dead connection.
    count = await mw._count("ratelimit:test:ip:y:0", 60)
    assert count == 1  # memory fallback
    assert mw._redis is None, "dead client must be cleared on transport error"
    assert mw._next_probe_at > clock["t"], "re-probe backoff must be armed"


@pytest.mark.asyncio
async def test_timeout_in_count_also_clears_cached_client(monkeypatch) -> None:
    """A wedged Redis (``asyncio.wait_for`` timeout) self-heals the same way.

    The hit path wraps the pipeline in ``asyncio.wait_for(..., timeout=2.0)``;
    a wedged Redis surfaces as ``asyncio.TimeoutError``. We raise it directly
    from ``execute`` (rather than sleeping past the real budget) so the test
    stays fast and deterministic while still exercising the timeout branch.
    """
    mw = RateLimitMiddleware(_make_app_stub())

    clock = {"t": 9000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])

    class _WedgedRedis:
        async def ping(self) -> bool:
            return True

        def pipeline(self) -> "_WedgedPipeline":
            return _WedgedPipeline()

    class _WedgedPipeline:
        def incr(self, key: str) -> None:  # noqa: ARG002
            pass

        def expire(self, key: str, window_s: int) -> None:  # noqa: ARG002
            pass

        async def execute(self) -> list[int]:
            raise asyncio.TimeoutError("pipeline wedged")

    wedged = _WedgedRedis()
    monkeypatch.setattr("redis.asyncio.from_url", lambda url, **kw: wedged)  # noqa: ARG005

    count = await mw._count("ratelimit:test:ip:z:0", 60)
    assert count == 1  # memory fallback
    assert mw._redis is None
    assert mw._next_probe_at > clock["t"]
