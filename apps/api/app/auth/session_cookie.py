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
from datetime import UTC, datetime, timedelta

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

# Strong refs to in-flight revocation persistence tasks so the asyncio event
# loop cannot garbage-collect them mid-flight (and so their exceptions are
# observed rather than swallowed by the loop's default handler). Tasks are
# discarded from the set when they finish.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def issue_session_cookie(response: Response, user_id: str, settings: Settings) -> None:
    """Sign a JWT and attach it as an HttpOnly session cookie on *response*."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=30)).timestamp()),
        "jti": secrets.token_urlsafe(16),
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


def get_user_id_from_cookie(request: Request, settings: Settings) -> str | None:
    """Decode and verify the session JWT.  Returns the subject or None on any error."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.session_secret, algorithms=[_ALGORITHM])
        jti = payload.get("jti")
        if jti and _is_revoked(jti):
            return None
        user_id: str | None = payload.get("sub")
        return user_id or None
    except JWTError as exc:
        logger.debug("session cookie decode error: {}", exc)
        return None
    except Exception as exc:  # pragma: no cover
        logger.warning("unexpected session cookie error: {}", exc)
        return None


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
    """Best-effort: push the revocation into Redis with a 30d TTL."""
    try:
        from app.sessions.events import get_redis

        redis = await get_redis()
        if redis is None:
            return
        await redis.set(_REVOCATION_KEY_PREFIX + jti, "1", ex=_COOKIE_MAX_AGE)
    except Exception as exc:  # pragma: no cover — telemetry only
        logger.debug("could not persist JTI revocation: {}", exc)


def _is_revoked(jti: str) -> bool:
    """Return True when ``jti`` has been logged-out (in-proc check only)."""
    return jti in _REVOKED_JTIS


def clear_revoked_jtis() -> None:
    """Test helper — drop the in-process revocation cache."""
    _REVOKED_JTIS.clear()
