"""RQ queue accessor — lazy so dev environments without Redis still boot."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from loguru import logger

from app.config import get_settings


@lru_cache(maxsize=1)
def get_queue() -> Any | None:
    """Return a Redis-backed RQ Queue, or None if Redis is unreachable."""
    settings = get_settings()
    try:
        import redis
        from rq import Queue

        conn = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
        conn.ping()
        # Queue name MUST match the worker CMD in
        # infra/compose/docker-compose.yml (worker service) AND
        # infra/docker/sandbox-worker.Dockerfile (CMD). Producer and consumer
        # are kept aligned by tests/test_queue_name.py.
        return Queue("provision", connection=conn)
    except Exception as exc:
        logger.debug("RQ queue unavailable: {}", exc)
        return None
