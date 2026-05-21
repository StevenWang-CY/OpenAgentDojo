"""Magic-link token generation and verification (plan §5.1).

Tokens are raw 32-byte urlsafe secrets.  Only their SHA-256 digest is
stored in the database so the DB cannot be used to replay a link.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.magic_link_token import MagicLinkToken
from app.models.user import User

_HANDLE_INVALID_RE = re.compile(r"[^a-z0-9]+")


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_magic_token() -> str:
    """Return a cryptographically-secure URL-safe 32-byte token."""
    return secrets.token_urlsafe(32)


def _slugify_handle(email: str) -> str:
    """Derive a candidate handle from the email local-part.

    Lowercases and strips every non-alphanumeric character. Returns a fallback
    of ``"user"`` if stripping leaves nothing — the caller is responsible for
    collision resolution.
    """
    local = email.split("@", 1)[0].lower()
    cleaned = _HANDLE_INVALID_RE.sub("", local)
    return cleaned or "user"


async def _allocate_handle(db: AsyncSession, base: str) -> str:
    """Find an unused handle by appending ``-2``, ``-3``, ... on collision."""
    # Prefetch every handle in the candidate family in one query so we don't
    # loop with N round-trips on a popular prefix.
    stmt = select(User.handle).where(User.handle.like(f"{base}%"))
    taken = {h for (h,) in (await db.execute(stmt)).all() if h}

    if base not in taken:
        return base

    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
        suffix += 1


async def create_magic_link(db: AsyncSession, email: str, base_url: str) -> str:
    """Upsert the user by email, store a hashed token, return the magic link URL.

    The link is valid for ``settings.magic_link_ttl_minutes`` minutes (default 30).
    Before issuing a new token we revoke all prior unconsumed tokens for the
    user — this means an attacker who steals an old (but still unexpired)
    email cannot replay it once the user has requested a fresh link.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    # Upsert user by email.
    existing_user: User | None = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if existing_user is None:
        base_handle = _slugify_handle(email)
        handle = await _allocate_handle(db, base_handle)
        try:
            user = User(email=email, handle=handle)
            db.add(user)
            await db.flush()  # assign PK before FK insert
        except IntegrityError:
            # Concurrent signup race — another request inserted this email
            # between the SELECT above and our INSERT. Roll back the failed
            # insert and re-fetch the now-existing row.
            await db.rollback()
            fetched = (
                await db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if fetched is None:
                # Extremely unlikely — fall through and re-raise the original
                # error path by attempting the insert one more time.
                raise
            user = fetched
    else:
        user = existing_user

    # Revoke prior unconsumed tokens so a fresh link supersedes any in-flight one.
    await db.execute(
        update(MagicLinkToken)
        .where(
            MagicLinkToken.user_id == user.id,
            MagicLinkToken.used_at.is_(None),
        )
        .values(used_at=now)
    )

    raw_token = generate_magic_token()
    token_hash = _hash_token(raw_token)
    expires_at = now + timedelta(minutes=settings.magic_link_ttl_minutes)

    magic = MagicLinkToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(magic)
    await db.flush()

    return f"{base_url}/auth/callback?token={raw_token}"


async def consume_magic_token(db: AsyncSession, raw_token: str) -> User | None:
    """Find a non-expired, unused token by hash; mark it used; return its user.

    Returns ``None`` if the token is unknown, already used, or expired.
    """
    token_hash = _hash_token(raw_token)
    now = datetime.now(UTC)

    row: MagicLinkToken | None = (
        await db.execute(
            select(MagicLinkToken).where(
                MagicLinkToken.token_hash == token_hash,
                MagicLinkToken.used_at.is_(None),
                MagicLinkToken.expires_at > now,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        return None

    # Mark consumed.
    row.used_at = now
    db.add(row)

    user: User | None = (
        await db.execute(select(User).where(User.id == row.user_id))
    ).scalar_one_or_none()

    if user is not None:
        user.last_login_at = now
        db.add(user)

    await db.flush()
    return user
