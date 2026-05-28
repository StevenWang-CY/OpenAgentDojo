"""RQ queue accessor — lazy so dev environments without Redis still boot.

Callers (``apps/api/app/auth/routes.py`` data-export, ``apps/api/app/reports/router.py``
report-render, ``apps/api/app/workers/provision.py``) use ``get_queue()`` to
decide between RQ-enqueue and an inline fallback. The decision MUST account
for both halves of the pipeline:

  1. Redis is reachable (so ``rq.Queue.enqueue`` won't raise), AND
  2. At least one ``rq worker`` process is currently consuming the
     ``provision`` queue.

Half (2) is the load-bearing check this module previously skipped. Without
it, a perfectly healthy Redis with no worker silently swallows enqueues —
the row sits in ``queued`` forever, the FE polls and sees the row never
move, and the user-facing "Queued — your export will start shortly"
banner becomes a permanent state. The watchdog in ``apps/api/app/main.py``
sweeps any stuck row as a belt-and-braces safety net, but the primary fix
is to never enqueue without a consumer in the first place.

The Redis connection + Queue object are cached for the process lifetime
(``_get_queue_cached``). The worker presence check runs fresh on every
call (``get_queue``) so the result tracks worker lifecycle in real time
— RQ keeps the worker registry under a well-known Redis key and the
probe is O(1).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from loguru import logger

from app.config import get_settings


# Queue name MUST match the worker CMD in:
#   - infra/compose/docker-compose.yml  (worker service)
#   - infra/docker/sandbox-worker.Dockerfile  (CMD)
# Producer and consumer are kept aligned by tests/test_queue_name.py.
_QUEUE_NAME = "provision"


@lru_cache(maxsize=1)
def _get_queue_cached() -> Any | None:
    """Redis connection + Queue object — cached for the process lifetime.

    Returns ``None`` if Redis is unreachable (dev environments without
    Redis fall through to the inline path).
    """
    settings = get_settings()
    try:
        import redis
        from rq import Queue

        conn = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
        conn.ping()
        return Queue(_QUEUE_NAME, connection=conn)
    except Exception as exc:
        logger.debug("RQ queue unavailable (redis): {}", exc)
        return None


def _queue_has_live_worker(queue: Any) -> bool:
    """Return True iff at least one RQ worker is currently consuming the
    queue. Probes the RQ worker registry stored in Redis.

    Failures (Redis blip, RQ API drift) fail-OPEN — we assume a worker
    exists and let the watchdog recover. The alternative (fail-closed
    inline) would 5-second-stall every request when Redis was actually
    fine, which is worse than letting the watchdog handle the rare race.
    """
    try:
        from rq import Worker

        return Worker.count(queue=queue, connection=queue.connection) > 0
    except Exception as exc:
        logger.debug("RQ worker probe failed (fail-open): {}", exc)
        return True


def get_queue() -> Any | None:
    """Return the RQ queue only when both Redis is reachable AND at least
    one worker is consuming ``provision``. Returns ``None`` otherwise so
    callers fall back to inline execution.

    Callers MUST treat ``None`` as "run the work in-process" rather than
    "the work will never run." See ``apps/api/app/auth/routes.py``
    ``post_me_data_export`` for the canonical handling pattern.
    """
    queue = _get_queue_cached()
    if queue is None:
        return None
    if not _queue_has_live_worker(queue):
        logger.debug(
            "RQ queue '{}' has no live worker; routing to inline fallback",
            _QUEUE_NAME,
        )
        return None
    return queue


def reset_queue_cache() -> None:
    """Drop the cached connection so the next ``get_queue()`` re-probes.

    Hook for tests + operator runbooks (e.g. after a Redis URL rotation).
    """
    _get_queue_cached.cache_clear()
