"""Session cookie helpers — issue, read, and revoke the HTTP-only session cookie.

The cookie value is a ``python-jose`` signed JWT with a 30-day expiry.  We use
HS256 with ``settings.session_secret`` as the signing key. Every token carries
a random ``jti`` so logout can revoke that one token without invalidating the
user's other live sessions.

Cookie attributes:
  - HttpOnly: True    — not readable by JS (anti-XSS)
  - Secure:           — True everywhere except local development
  - SameSite: lax     — CSRF protection while still allowing top-level navigations
  - Max-Age: 30 days
"""

from __future__ import annotations

import asyncio
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request, Response
from jose import JWTError, jwt
from loguru import logger

from app.config import Settings

_ALGORITHM = "HS256"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days in seconds

# In-process fallback set for revoked JTIs. The real implementation uses Redis
# when available so revocation survives a worker restart; this set is checked
# first as a fast path.
_REVOKED_JTIS: set[str] = set()
_REVOCATION_KEY_PREFIX = "auth:revoked_jti:"

# Per-process positive cache: JTIs that Redis confirmed are revoked. Cached
# for ``_REDIS_REVOKED_TTL_S`` so we don't hit Redis on every authenticated
# request once a token is known-bad. Values are the insertion time so the
# cache self-expires without needing an additional dependency. We keep the
# data structure tiny on purpose — revoked JTIs are rare, and any miss falls
# back to a Redis GET that's already fast.
_REDIS_REVOKED_CACHE: dict[str, float] = {}
_REDIS_REVOKED_TTL_S = 60.0

# Strong refs to in-flight revocation persistence tasks so the asyncio event
# loop cannot garbage-collect them mid-flight (and so their exceptions are
# observed rather than swallowed by the loop's default handler). Tasks are
# discarded from the set when they finish.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def issue_session_cookie(
    response: Response,
    user_id: str,
    settings: Settings,
    *,
    epoch: int = 1,
) -> None:
    """Sign a JWT and attach it as an HttpOnly session cookie on *response*.

    The ``epoch`` parameter stamps the user's current ``session_epoch`` into
    the cookie claim. The verifier compares ``claim.epoch`` against the
    *current* ``users.session_epoch`` and rejects any cookie whose claim
    is older — that is the mechanism behind "sign out everywhere", the
    email-change session rotation, and the deletion grace lockout. Callers
    that have a :class:`User` in hand should prefer
    :func:`mint_session_cookie_for_user`, which pulls ``epoch`` off the row.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=30)).timestamp()),
        "jti": secrets.token_urlsafe(16),
        "epoch": int(epoch),
    }
    token = jwt.encode(payload, settings.session_secret, algorithm=_ALGORITHM)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )


def mint_session_cookie_for_user(response: Response, user: Any, settings: Settings) -> None:
    """Issue a fresh cookie carrying ``user.session_epoch``.

    Convenience wrapper for handlers that already hold a :class:`User` row
    (the email-change confirm, sign-out-all, and the grace lockout
    transition all want this). Keeps the epoch source of truth in one place.
    """
    epoch = int(getattr(user, "session_epoch", 1) or 1)
    issue_session_cookie(response, str(user.id), settings, epoch=epoch)


def rotate_user_session_epoch(user: Any) -> int:
    """Bump ``user.session_epoch`` in-memory and return the new value.

    The caller is responsible for committing the underlying session — this
    helper does no DB I/O of its own so it composes with both async and
    sync sessions. After commit, every cookie minted before this call will
    fail :func:`verify_epoch_claim`.
    """
    current = int(getattr(user, "session_epoch", 1) or 1)
    new_epoch = current + 1
    user.session_epoch = new_epoch
    return new_epoch


def verify_epoch_claim(payload: dict[str, Any], user: Any) -> bool:
    """Return True when ``payload.epoch`` is current for ``user``.

    A missing claim is treated as epoch=0 so legacy cookies minted before
    the column existed are accepted exactly once — the first response then
    re-mints with the current epoch (see ``_build_me_response``). Any
    explicit claim *below* the user's current epoch is rejected.
    """
    user_epoch = int(getattr(user, "session_epoch", 1) or 1)
    claim_epoch = payload.get("epoch")
    if claim_epoch is None:
        # Legacy cookie minted before the epoch claim existed. Accept once;
        # the next /me round-trip will re-mint with the current epoch.
        return True
    try:
        return int(claim_epoch) >= user_epoch
    except (TypeError, ValueError):
        return False


def _decode_cookie_payload(request: Request, settings: Settings) -> dict | None:
    """Decode the cookie JWT — returns the payload dict or None on any error.

    Shared between :func:`get_user_id_from_cookie` (sync, in-proc-only) and
    :func:`get_user_id_from_cookie_async` (async, also consults Redis).
    """
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    try:
        payload: dict[Any, Any] = jwt.decode(
            token, settings.session_secret, algorithms=[_ALGORITHM]
        )
        return payload
    except JWTError as exc:
        logger.debug("session cookie decode error: {}", exc)
        return None
    except Exception as exc:  # pragma: no cover
        logger.warning("unexpected session cookie error: {}", exc)
        return None


def get_user_id_from_cookie(request: Request, settings: Settings) -> str | None:
    """Decode and verify the session JWT — IN-PROCESS revocation check only.

    Retained for callers that run in a synchronous context (notably the rate
    limiter middleware). Async request paths should prefer
    :func:`get_user_id_from_cookie_async` so Redis-backed revocation also
    applies — otherwise a logged-out token still authenticates on workers
    whose in-proc ``_REVOKED_JTIS`` set was rebuilt on restart.
    """
    payload = _decode_cookie_payload(request, settings)
    if payload is None:
        return None
    jti = payload.get("jti")
    if jti and _is_revoked(jti):
        return None
    user_id: str | None = payload.get("sub")
    return user_id or None


async def get_user_id_from_cookie_async(request: Request, settings: Settings) -> str | None:
    """Async variant — checks in-process AND Redis-backed JTI revocation.

    Use this from FastAPI dependencies / async handlers so logging out on
    one worker propagates to all workers via Redis. Failure to reach Redis
    is non-fatal: we fall back to the in-process set so a transient Redis
    blip doesn't 500 every authenticated request.
    """
    payload = _decode_cookie_payload(request, settings)
    if payload is None:
        return None
    jti = payload.get("jti")
    if jti and await _is_revoked_async(jti):
        return None
    user_id: str | None = payload.get("sub")
    return user_id or None


def revoke_session_cookie(
    response: Response, settings: Settings, request: Request | None = None
) -> None:
    """Delete the session cookie and revoke its JTI."""
    if request is not None:
        raw = request.cookies.get(settings.session_cookie_name)
        if raw:
            try:
                payload = jwt.decode(raw, settings.session_secret, algorithms=[_ALGORITHM])
                jti = payload.get("jti")
                if jti:
                    _mark_revoked(jti)
            except Exception as exc:  # pragma: no cover — best-effort
                logger.debug("could not decode cookie for revocation: {}", exc)

    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def _mark_revoked(jti: str) -> None:
    """Record ``jti`` in the revocation set (in-proc, and Redis if reachable)."""
    _REVOKED_JTIS.add(jti)
    try:
        loop = asyncio.get_running_loop()
        # Track the task so the loop holds a strong reference (asyncio docs
        # explicitly warn that fire-and-forget ``create_task`` can be GC'd
        # before completion) and so its exception is observable via
        # ``add_done_callback`` instead of being printed by the default
        # exception handler.
        task = loop.create_task(_persist_revocation(jti), name=f"persist-revocation-{jti[:8]}")
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_on_persist_done)
    except RuntimeError:
        # No running loop — caller is in a sync context (probably tests). The
        # in-proc set still works for the lifetime of the worker.
        pass


def _on_persist_done(task: asyncio.Task[None]) -> None:
    """Discard the task ref and log any exception it raised."""
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("revocation persistence task failed: {}", exc)


async def _persist_revocation(jti: str) -> None:
    """Best-effort: push the revocation into Redis with a 30d TTL.

    Failures here matter: without the Redis row, a logout on worker A
    is invisible to workers B/C and a logged-out cookie can still
    authenticate elsewhere until that JTI's natural expiry. We deliberately
    keep the API request itself successful (logout is a UX-critical
    button) but raise the log level to WARNING and bump a counter so
    operators see the failure rate on dashboards rather than silently
    eating it at debug-level.
    """
    try:
        from app.sessions.events import get_redis

        redis = await get_redis()
        if redis is None:
            return
        await redis.set(_REVOCATION_KEY_PREFIX + jti, "1", ex=_COOKIE_MAX_AGE)
    except Exception as exc:
        logger.warning(
            "could not persist JTI revocation to Redis — logout will only "
            "apply to this worker until natural expiry: {}",
            exc,
        )
        try:
            from app.observability import jti_revocation_persist_failures_total

            jti_revocation_persist_failures_total.inc()
        except Exception:  # pragma: no cover — observability is best-effort
            pass


def _is_revoked(jti: str) -> bool:
    """Return True when ``jti`` has been logged-out (in-proc check only).

    Sync helper for callers that can't await Redis. Also consults the
    Redis-positive cache so a previously-confirmed revocation continues to
    block authentication until the cache TTL expires.
    """
    if jti in _REVOKED_JTIS:
        return True
    cached_at = _REDIS_REVOKED_CACHE.get(jti)
    if cached_at is None:
        return False
    if (time.monotonic() - cached_at) > _REDIS_REVOKED_TTL_S:
        # Expire stale entry so the next async check has to re-confirm.
        _REDIS_REVOKED_CACHE.pop(jti, None)
        return False
    return True


async def _is_revoked_async(jti: str) -> bool:
    """Check in-proc set + Redis. Falls back to in-proc on Redis errors.

    Positive Redis hits are cached in-process for ``_REDIS_REVOKED_TTL_S``
    so we don't repeat a network round-trip on every authenticated request
    once a token is known-bad.
    """
    # 1. Fast path — already revoked on this worker.
    if jti in _REVOKED_JTIS:
        return True
    # 2. Local positive cache (Redis-confirmed within TTL).
    cached_at = _REDIS_REVOKED_CACHE.get(jti)
    if cached_at is not None:
        if (time.monotonic() - cached_at) <= _REDIS_REVOKED_TTL_S:
            return True
        _REDIS_REVOKED_CACHE.pop(jti, None)
    # 3. Best-effort Redis lookup.
    try:
        from app.sessions.events import get_redis

        redis = await get_redis()
        if redis is None:
            return False
        value = await redis.get(_REVOCATION_KEY_PREFIX + jti)
    except Exception as exc:  # pragma: no cover — telemetry only
        logger.debug("redis revocation lookup failed for {}: {}", jti[:8], exc)
        return False
    if value is None:
        return False
    # Cache the positive result so subsequent requests skip the round-trip.
    _REDIS_REVOKED_CACHE[jti] = time.monotonic()
    return True


def clear_revoked_jtis() -> None:
    """Test helper — drop the in-process revocation cache."""
    _REVOKED_JTIS.clear()
    _REDIS_REVOKED_CACHE.clear()
