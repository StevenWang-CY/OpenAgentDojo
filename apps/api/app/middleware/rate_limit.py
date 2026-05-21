"""Per-route token-bucket rate limiting backed by Redis.

Each rule is a per-bucket request-count cap over a sliding 60-second window.
Limits are keyed by either the authenticated user id (when available) or the
client IP address. When Redis is unavailable we fail OPEN — rate-limiting is a
defence-in-depth measure, not the only authn surface.

Limits (from §16 of the implementation plan):

  auth_magic_link     5  / min / IP
  auth_callback      30  / min / IP
  sessions_create     6  / min / user
  commands           30  / min / user
  files              60  / min / user
  prompts            12  / min / user
  submit              3  / min / user
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import get_settings

# ---------------------------------------------------------------------------
# Rule table.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    name: str
    method: str  # "POST", "GET", or "*" for any
    pattern: re.Pattern[str]
    limit: int
    window_s: int = 60
    key_by: str = "ip"  # "ip" or "user"


_RULES: tuple[_Rule, ...] = (
    _Rule(
        name="auth_magic_link",
        method="POST",
        pattern=re.compile(r"^/api/v1/auth/magic-link/?$"),
        limit=5,
        key_by="ip",
    ),
    _Rule(
        name="auth_callback",
        method="GET",
        pattern=re.compile(r"^/api/v1/auth/callback/?$"),
        limit=30,
        key_by="ip",
    ),
    _Rule(
        name="sessions_create",
        method="POST",
        pattern=re.compile(r"^/api/v1/sessions/?$"),
        limit=6,
        key_by="user",
    ),
    _Rule(
        name="commands",
        method="POST",
        pattern=re.compile(r"^/api/v1/sessions/[^/]+/commands/?$"),
        limit=30,
        key_by="user",
    ),
    _Rule(
        name="files",
        method="POST",
        pattern=re.compile(r"^/api/v1/sessions/[^/]+/files(/revert)?/?$"),
        limit=60,
        key_by="user",
    ),
    _Rule(
        name="prompts",
        method="POST",
        pattern=re.compile(r"^/api/v1/sessions/[^/]+/prompts/?$"),
        limit=12,
        key_by="user",
    ),
    _Rule(
        name="submit",
        method="POST",
        pattern=re.compile(r"^/api/v1/sessions/[^/]+/submit/?$"),
        limit=3,
        key_by="user",
    ),
)


def _match_rule(method: str, path: str) -> _Rule | None:
    for rule in _RULES:
        if rule.method not in ("*", method):
            continue
        if rule.pattern.match(path):
            return rule
    return None


# ---------------------------------------------------------------------------
# In-memory fallback counter (used when Redis is unreachable, AND in tests).
# ---------------------------------------------------------------------------


class _InMemoryCounter:
    """Process-local counter — only correct within a single worker."""

    def __init__(self) -> None:
        self._buckets: dict[str, list[float]] = {}

    def hit(self, key: str, window_s: int) -> int:
        now = time.monotonic()
        cutoff = now - window_s
        bucket = [t for t in self._buckets.get(key, []) if t > cutoff]
        bucket.append(now)
        self._buckets[key] = bucket
        return len(bucket)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply per-route token-bucket limits using Redis (preferred) or memory."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._memory = _InMemoryCounter()
        self._redis: Any | None = None
        self._redis_probed = False

    async def _get_redis(self) -> Any | None:
        if self._redis_probed:
            return self._redis
        self._redis_probed = True
        try:
            import redis.asyncio as aioredis

            settings = get_settings()
            client = aioredis.from_url(
                settings.redis_url,
                socket_connect_timeout=1,
                socket_timeout=1,
                decode_responses=True,
            )
            # redis-py's ping() may return Awaitable[bool] or bool depending on
            # configuration; await only when awaitable.
            ping_result = client.ping()
            if asyncio.iscoroutine(ping_result):
                await ping_result
            self._redis = client
        except Exception as exc:
            logger.debug("rate-limit redis unavailable, using in-memory: {}", exc)
            self._redis = None
        return self._redis

    def _identity(self, request: Request, rule: _Rule) -> str:
        if rule.key_by == "user":
            # 1) request.state.user — set by an upstream auth middleware (future).
            user = getattr(request.state, "user", None)
            if user is not None and getattr(user, "id", None) is not None:
                return f"user:{user.id}"
            # 2) Cookie peek — decode the session cookie ourselves so per-user
            #    limits work even when no auth middleware has populated state.
            try:
                from app.auth.session_cookie import get_user_id_from_cookie

                uid = get_user_id_from_cookie(request, get_settings())
                if uid:
                    return f"user:{uid}"
            except Exception:  # pragma: no cover — defence-in-depth
                pass
        host = request.client.host if request.client else "anon"
        return f"ip:{host}"

    async def _count(self, bucket_key: str, window_s: int) -> int:
        redis = await self._get_redis()
        if redis is None:
            return self._memory.hit(bucket_key, window_s)

        # Redis-backed fixed window: INCR + EXPIRE on first hit.
        try:
            pipe = redis.pipeline()
            pipe.incr(bucket_key)
            pipe.expire(bucket_key, window_s)
            results = await pipe.execute()
            return int(results[0])
        except Exception as exc:
            logger.warning("rate-limit redis hit failed, falling back: {}", exc)
            return self._memory.hit(bucket_key, window_s)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rule = _match_rule(request.method.upper(), request.url.path)
        if rule is None:
            return await call_next(request)

        identity = self._identity(request, rule)
        # Window the key by the integer minute so the counter is bounded by Redis TTL.
        minute = int(time.time() // rule.window_s)
        bucket_key = f"ratelimit:{rule.name}:{identity}:{minute}"

        count = await self._count(bucket_key, rule.window_s)
        if count > rule.limit:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"rate limit exceeded for {rule.name}",
                    "code": "rate_limited",
                    "limit": rule.limit,
                    "window_seconds": rule.window_s,
                },
                headers={"Retry-After": str(rule.window_s)},
            )

        return await call_next(request)
