"""FastAPI dependencies for authentication.

``get_current_user``  — soft auth: returns the User or None.
``require_auth``      — hard auth: raises 401 if not signed in.

Dev fallback: when ``arena_env == "development"`` AND ``allow_dev_auth`` is
True, missing cookies fall back to a deterministic dev user keyed by client
IP. The fallback is OFF outside development (enforced in ``config.py``) and
logs a loud warning every time it fires so it can't slip past code review.

Per-user session invalidation (P0-6): cookies carry an ``epoch`` claim
matching ``users.session_epoch`` at mint time. After decoding the JWT we
load the user and call ``verify_epoch_claim`` — any cookie whose epoch is
behind the user's current value is rejected as if it had been revoked.
This is how "sign out everywhere", email confirm, deletion schedule and
hard-delete invalidate every other live device without iterating JTIs.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session_cookie import (
    _decode_cookie_payload,
    _is_revoked_async,
    verify_epoch_claim,
)
from app.config import get_settings
from app.db.session import get_db
from app.models.user import User


def _dev_user_id_from_ip(request: Request) -> uuid.UUID:
    """Return a stable dev user id keyed by client IP (no DB lookup)."""
    ns = uuid.UUID("00000000-0000-0000-0000-000000000001")
    ip = request.client.host if request.client else "anon"
    return uuid.uuid5(ns, ip)


async def _ensure_dev_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Upsert a minimal dev placeholder user and return it.

    The synthesised email lives at ``@example.com`` — the RFC 2606 reserved
    "always valid, never routable" domain. We tried ``@arena.local`` first
    (RFC 6762 mDNS reserved) and ``@arena.test`` (RFC 6761 testing reserved),
    but ``email-validator`` rejects both, which made /me return 500 when the
    dev-fallback fired. ``example.com`` is accepted unconditionally and
    cannot deliver real mail, which is exactly what we want for a synthetic
    placeholder.
    """
    existing: User | None = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if existing is not None:
        # Migrate stale dev rows whose persisted email uses a TLD that
        # ``email-validator`` rejects (it special-cases ``.local`` per RFC
        # 6762 and ``.test`` per RFC 6761). Without this, /me would 500
        # because UserRead's EmailStr can't validate the persisted address.
        # ``example.com`` (RFC 2606) is the canonical "always-valid, never
        # routable" host and is accepted unconditionally.
        current = existing.email or ""
        if current.endswith("@arena.local") or current.endswith("@arena.test"):
            existing.email = f"dev-{user_id}@example.com"
            await db.commit()
            await db.refresh(existing)
        return existing

    user = User(
        id=user_id,
        email=f"dev-{user_id}@example.com",
        display_name="Dev user",
        handle=f"dev-{str(user_id)[:8]}",
    )
    db.add(user)
    await db.flush()
    # Without commit the row only lives in this session; flush gives us the
    # server-side default for ``created_at`` so model_validate(...) can
    # serialise the row without tripping the non-null datetime guard.
    await db.commit()
    await db.refresh(user)
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

    # Inline cookie decode so we can perform the post-DB-lookup epoch check
    # without making a second pass through the cookie. ``get_user_id_from_
    # cookie_async`` would otherwise hide the JTI revocation status from us.
    payload = _decode_cookie_payload(request, settings)
    if payload is not None:
        jti = payload.get("jti")
        if jti and await _is_revoked_async(jti):
            payload = None

    if payload is not None:
        user_id_str = payload.get("sub")
        if user_id_str:
            try:
                uid = uuid.UUID(user_id_str)
            except ValueError:
                return None
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
            if user is None:
                return None
            # P0-6 — per-user session epoch. A cookie minted before
            # ``user.session_epoch`` was bumped is treated as revoked.
            if not verify_epoch_claim(payload, user):
                logger.debug(
                    "auth: rejecting cookie for user {} (epoch {} < user {})",
                    uid,
                    payload.get("epoch"),
                    user.session_epoch,
                )
                return None
            return user

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
