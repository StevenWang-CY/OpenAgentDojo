"""FastAPI dependencies for authentication.

``get_current_user``  — soft auth: returns the User or None.
``require_auth``      — hard auth: raises 401 if not signed in.

Dev fallback: when ``arena_env == "development"`` AND ``allow_dev_auth`` is
True, missing cookies fall back to a deterministic dev user keyed by client
IP. The fallback is OFF outside development (enforced in ``config.py``) and
logs a loud warning every time it fires so it can't slip past code review.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session_cookie import get_user_id_from_cookie_async
from app.config import get_settings
from app.db.session import get_db
from app.models.user import User


def _dev_user_id_from_ip(request: Request) -> uuid.UUID:
    """Return a stable dev user id keyed by client IP (no DB lookup)."""
    ns = uuid.UUID("00000000-0000-0000-0000-000000000001")
    ip = request.client.host if request.client else "anon"
    return uuid.uuid5(ns, ip)


async def _ensure_dev_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Upsert a minimal dev placeholder user and return it."""
    existing: User | None = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    user = User(
        id=user_id,
        email=f"dev-{user_id}@arena.local",
        display_name="Dev user",
        handle=f"dev-{str(user_id)[:8]}",
    )
    db.add(user)
    await db.flush()
    return user


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Resolve the authenticated user from the session cookie.

    In development mode (with ``ALLOW_DEV_AUTH=True``), falls back to a
    deterministic dev user derived from the client IP when no cookie is
    present. The fallback emits a WARNING level log line every invocation
    so it cannot silently mask a production misconfiguration.
    """
    settings = get_settings()
    user_id_str = await get_user_id_from_cookie_async(request, settings)

    if user_id_str is not None:
        try:
            uid = uuid.UUID(user_id_str)
        except ValueError:
            return None
        return (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()

    # Dev fallback — only fire when explicitly enabled AND in development.
    if settings.arena_env == "development" and settings.allow_dev_auth:
        dev_uid = _dev_user_id_from_ip(request)
        logger.warning(
            "DEV AUTH FALLBACK: synthesised user {} from client IP {} "
            "for {} {} — DO NOT enable in staging/production",
            dev_uid,
            request.client.host if request.client else "anon",
            request.method,
            request.url.path,
        )
        return await _ensure_dev_user(db, dev_uid)

    return None


async def require_auth(
    user: User | None = Depends(get_current_user),
) -> User:
    """Raise HTTP 401 if the caller is not authenticated."""
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user
