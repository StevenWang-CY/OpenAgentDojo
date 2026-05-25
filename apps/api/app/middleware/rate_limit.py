"""Per-route token-bucket rate limiting backed by Redis.

Each rule is a per-bucket request-count cap over a sliding 60-second window.
Limits are keyed by either the authenticated user id (when available) or the
client IP address. When Redis is unavailable we fail OPEN — rate-limiting is a
defence-in-depth measure, not the only authn surface.

IP-keyed caveat: behind a reverse proxy that doesn't forward the real client
IP (``X-Forwarded-For`` etc.), every request looks like it's coming from the
proxy — turning the per-IP limit into a global cap. Deploy with a proxy that
forwards client IPs (and configure Starlette's ``ProxyHeadersMiddleware`` /
your trusted proxy list) or switch the rule's ``key_by`` to ``"user"``.

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
import hashlib
import json
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

# Per-email cap on the magic-link route — defence-in-depth on top of the
# per-IP bucket so a single email address can't be spammed even from a
# rotating pool of source IPs.
_MAGIC_LINK_PER_EMAIL_LIMIT = 3
_MAGIC_LINK_PER_EMAIL_WINDOW_S = 900  # 15 minutes

# Fail-open warning throttle.
#
# When Redis is unreachable we silently fall back to a per-worker in-memory
# counter. That trade-off (defence-in-depth, not the only authn surface) is
# acceptable, but we used to log it at DEBUG and so operators never knew
# they'd entered the degraded regime. We now emit a WARNING — but at most
# once per ``_FAIL_OPEN_LOG_THROTTLE_S`` window per process to avoid filling
# the log shipper on a sustained outage.
_FAIL_OPEN_LOG_THROTTLE_S = 60.0
# Map of "context tag" (typically the failure reason) to the monotonic
# timestamp of the most recent WARNING we emitted for it. We use ``monotonic``
# so a wall-clock skew can't suppress legitimate warnings.
_LAST_FAIL_OPEN_WARN: dict[str, float] = {}


def _log_fail_open(tag: str, exc: BaseException) -> None:
    """Emit a throttled WARNING when we slip into the in-memory fallback.

    Rate-limiting is fail-open by design (it's defence-in-depth), but an
    operator must still know they're running degraded so they can investigate.
    The throttle key is the caller-supplied ``tag`` so a hit-path failure
    and a probe-path failure don't mute each other.
    """
    now = time.monotonic()
    last = _LAST_FAIL_OPEN_WARN.get(tag, 0.0)
    if now - last < _FAIL_OPEN_LOG_THROTTLE_S:
        return
    _LAST_FAIL_OPEN_WARN[tag] = now
    logger.warning(
        "[rate_limit] redis unavailable — falling back to in-memory counter "
        "({}). Per-worker only; cross-worker traffic will not be rate limited "
        "until redis recovers. Last error: {}",
        tag,
        exc,
    )


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
    # Profile endpoints perform JSON aggregation + history joins on every
    # request with no caching. A scraper hitting /profiles/{handle} in a
    # loop would amplify trivially. Public endpoint → IP key; authed
    # endpoint → user key.
    _Rule(
        name="profile_public",
        method="GET",
        pattern=re.compile(r"^/api/v1/profiles/[^/]+/?$"),
        limit=120,
        key_by="ip",
    ),
    _Rule(
        name="profile_me_skills",
        method="GET",
        pattern=re.compile(r"^/api/v1/profiles/me/skills/?$"),
        limit=60,
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
            # Throttled WARNING so the operator sees the degraded mode
            # without filling the log shipper on a sustained outage (P2-B11).
            _log_fail_open("probe", exc)
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

        # Redis-backed fixed window: INCR + EXPIRE on first hit. Wrapped in
        # ``wait_for`` so a wedged Redis can't stall the request indefinitely
        # — the in-memory bucket is per-worker, not globally consistent, so
        # the caveat is documented at the module top.
        try:
            pipe = redis.pipeline()
            pipe.incr(bucket_key)
            pipe.expire(bucket_key, window_s)
            results = await asyncio.wait_for(pipe.execute(), timeout=2.0)
            return int(results[0])
        except Exception as exc:
            # Throttled per-process WARNING — see ``_log_fail_open`` docstring.
            # We deliberately keep the immediate (non-throttled) ``warning``
            # nuance off here so a flapping redis doesn't drown the logs.
            _log_fail_open("hit", exc)
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

        # Defence-in-depth: cap magic-link sends per email address so a single
        # account can't be spammed from a rotating pool of source IPs. The
        # check is best-effort — we skip silently if the body can't be parsed
        # (the route handler will then surface its own 422).
        if rule.name == "auth_magic_link":
            email = await self._peek_magic_link_email(request)
            if email is not None:
                email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()
                email_window = int(time.time() // _MAGIC_LINK_PER_EMAIL_WINDOW_S)
                email_key = f"rl:magic:email:{email_hash}:{email_window}"
                email_count = await self._count(email_key, _MAGIC_LINK_PER_EMAIL_WINDOW_S)
                if email_count > _MAGIC_LINK_PER_EMAIL_LIMIT:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": "rate limit exceeded for auth_magic_link (per email)",
                            "code": "rate_limited",
                            "limit": _MAGIC_LINK_PER_EMAIL_LIMIT,
                            "window_seconds": _MAGIC_LINK_PER_EMAIL_WINDOW_S,
                        },
                        headers={"Retry-After": str(_MAGIC_LINK_PER_EMAIL_WINDOW_S)},
                    )

        return await call_next(request)

    @staticmethod
    async def _peek_magic_link_email(request: Request) -> str | None:
        """Buffer the JSON body and return the ``email`` field if present.

        The body is re-injected via a custom ``receive`` callable so the
        downstream handler still sees an unconsumed request — mirrors the
        pattern used by ``banned_commands.py``.
        """
        try:
            body_bytes = await request.body()
        except Exception:  # pragma: no cover — defence-in-depth
            return None

        async def _replay() -> dict[str, object]:  # ASGI receive
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        request._receive = _replay

        if not body_bytes:
            return None
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        email = payload.get("email")
        if not isinstance(email, str) or not email:
            return None
        return email.strip().lower()
