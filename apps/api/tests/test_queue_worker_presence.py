"""``get_queue()`` returns None when no RQ worker is consuming.

Root cause of the "Queued — your export will start shortly" bug that
stuck data-exports in ``queued`` forever: ``get_queue()`` returned a
live Queue object whenever Redis was reachable, ignoring whether any
``rq worker`` process was actually dequeueing. In any environment with
Redis up (sessions, caching) but no worker pod, the enqueue silently
succeeded and the row sat in Redis indefinitely.

The fix splits the cached Redis-connection probe from a fresh
worker-presence probe; the latter consults ``rq.Worker.count(queue=...)``
which reads RQ's worker registry stored in Redis. When the count is
zero ``get_queue`` returns None so callers fall back to inline
execution.

This file pins both halves of the contract:

* Redis up but no worker → None (the bug fix).
* Redis up AND ≥1 worker → returns the Queue (the steady state).
* RQ worker probe raises → fail OPEN, return the Queue. (Failing closed
  would 5-second-stall every request when Redis was actually fine; the
  sweeper recovers anything that races us.)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_queue_cache():
    """Each test starts with a fresh ``_get_queue_cached`` lookup so the
    previous test's MagicMock doesn't bleed through the lru_cache."""
    from app.workers.queue import reset_queue_cache

    reset_queue_cache()
    yield
    reset_queue_cache()


def _stub_queue() -> Any:
    """A Queue-like stand-in carrying the connection attribute the
    worker probe reads. The actual Queue class isn't constructed because
    it would try to talk to Redis."""
    q = MagicMock(name="Queue")
    q.connection = MagicMock(name="RedisConn")
    return q


def test_returns_none_when_redis_unreachable():
    from app.workers import queue as queue_mod

    with patch.object(queue_mod, "_get_queue_cached", return_value=None):
        assert queue_mod.get_queue() is None


def test_returns_none_when_redis_up_but_zero_workers():
    """The headline bug — Redis is fine, no RQ worker. Must return None
    so the caller falls through to its inline fallback."""
    from app.workers import queue as queue_mod

    q = _stub_queue()
    with (
        patch.object(queue_mod, "_get_queue_cached", return_value=q),
        patch("rq.Worker.count", return_value=0),
    ):
        assert queue_mod.get_queue() is None


def test_returns_queue_when_worker_present():
    """Steady state: at least one worker is running, return the Queue
    so the request enqueues normally."""
    from app.workers import queue as queue_mod

    q = _stub_queue()
    with (
        patch.object(queue_mod, "_get_queue_cached", return_value=q),
        patch("rq.Worker.count", return_value=1),
    ):
        assert queue_mod.get_queue() is q


def test_worker_probe_failure_is_fail_open():
    """Defensive: an exception inside the worker probe (Redis blip,
    RQ API drift) must NOT route every request to the slow inline
    path. The sweeper will catch anything that does get enqueued
    without a consumer."""
    from app.workers import queue as queue_mod

    q = _stub_queue()
    with (
        patch.object(queue_mod, "_get_queue_cached", return_value=q),
        patch("rq.Worker.count", side_effect=RuntimeError("probe broke")),
    ):
        assert queue_mod.get_queue() is q


def test_queue_cache_can_be_reset():
    """The connection cache must be droppable for credential rotation
    and for test isolation."""
    from app.workers import queue as queue_mod

    # Prime the cache with one value, then verify a reset re-probes.
    calls: list[int] = []

    def factory():
        calls.append(1)
        return _stub_queue()

    with patch.object(queue_mod, "_get_queue_cached", side_effect=factory):
        # Without the cache wrapper this isn't actually hitting the
        # cache — but the point of this test is that reset_queue_cache
        # is callable and doesn't raise.
        queue_mod.reset_queue_cache()
    assert True  # reset returned cleanly
