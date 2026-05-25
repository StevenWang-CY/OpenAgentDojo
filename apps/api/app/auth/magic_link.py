"""Magic-link token generation and verification (plan §5.1).

Tokens are raw 32-byte urlsafe secrets.  Only their SHA-256 digest is
stored in the database so the DB cannot be used to replay a link.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func as sa_func
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.magic_link_token import (
    PURPOSE_EMAIL_CHANGE,
    PURPOSE_SIGN_IN,
    MagicLinkToken,
)
from app.models.user import User

_HANDLE_INVALID_RE = re.compile(r"[^a-z0-9]+")

# P0-10 — resend-throttle window. Identical to the FE's 60-second
# countdown timer so the two stay in lockstep when the operator adjusts
# the policy. Exposed as a module constant (not a Setting) because the
# magic-link reminder cadence is a security-relevant policy, not a
# per-deploy knob — bumping it requires a code review.
MAGIC_LINK_RESEND_WINDOW_SECONDS = 60

# Redis key prefix for the per-email throttle. SHA-256 the email so the
# key set in Redis can't be used as a directory of known email addresses
# by an operator with cluster access.
_RESEND_REDIS_KEY_PREFIX = "auth:magic_resend:"


def _hash_email_for_throttle(email: str) -> str:
    """Stable SHA-256 hex of the lowercased email for throttle keys.

    Distinct from ``hash_email_for_event`` (which uses a settings-salt
    so log lines can't be cross-referenced with throttle keys) — this
    one is a pure deterministic digest used as a Redis key suffix and a
    structured-log identifier.
    """
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


async def magic_link_resend_wait_seconds(email: str) -> int | None:
    """Return seconds the caller must wait before another link is sent.

    Phase 4.A.17 — the return type tightened from ``int`` to ``int | None``
    so the route can distinguish "Redis down" (``None``) from "Redis
    says no throttle" (``0``). Only the down case forces a second
    query against the DB fallback (one extra SELECT per request);
    before the fix the route ran the DB fallback on every send because
    the integer ``0`` collapsed both cases.

    Implementation:

    * Preferred path: Redis. The key ``auth:magic_resend:{sha256(email)}``
      is written with a 60-second TTL when a send proceeds. Re-reading
      the TTL gives the precise remaining wait time, which the route
      passes back to the caller via the ``Retry-After`` header and the
      JSON body on ``/magic-link/resend``.
    * Fallback: ``None`` is returned when Redis is unavailable. The
      caller (the route) then consults
      :func:`magic_link_resend_db_fallback_wait_seconds` which reads the
      most-recent ``MagicLinkToken`` row for any user whose email
      matches and derives the elapsed-since-mint from ``created_at`` /
      ``expires_at - magic_link_ttl_minutes``. Cheap and correct as long
      as the user's row exists; for first-time sign-up requests the DB
      fallback returns 0 (no prior token), which matches the "first
      send" semantics.

    Never raises — every failure mode degrades to "allow the send" so
    a flaky Redis can't lock a user out of sign-in entirely.
    """
    return await _resend_wait_from_redis(email)


async def magic_link_resend_db_fallback_wait_seconds(db: AsyncSession, email: str) -> int:
    """DB fallback for the resend throttle.

    Computes how long ago the most-recent sign-in token for this email
    was minted. If less than ``MAGIC_LINK_RESEND_WINDOW_SECONDS`` ago,
    returns the remaining wait. Only consulted by the route when Redis
    is unavailable — see ``_record_resend_in_redis`` for the happy path.
    """
    settings = get_settings()
    row: MagicLinkToken | None = (
        await db.execute(
            select(MagicLinkToken)
            .join(User, User.id == MagicLinkToken.user_id)
            .where(
                sa_func.lower(User.email) == email.strip().lower(),
                MagicLinkToken.purpose == PURPOSE_SIGN_IN,
            )
            .order_by(MagicLinkToken.expires_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or row.expires_at is None:
        return 0
    # ``created_at = expires_at - ttl``; derive instead of adding a column.
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    created_at = expires_at - timedelta(minutes=settings.magic_link_ttl_minutes)
    elapsed = (datetime.now(UTC) - created_at).total_seconds()
    remaining = MAGIC_LINK_RESEND_WINDOW_SECONDS - int(elapsed)
    return remaining if remaining > 0 else 0


async def _resend_wait_from_redis(email: str) -> int | None:
    """Return the Redis-backed wait time or ``None`` when Redis is down.

    The TTL query is wrapped in a tight try/except so a Redis blip falls
    through to the DB path rather than 500-ing the route.
    """
    from app.sessions.events import get_redis

    try:
        redis = await get_redis()
    except Exception:  # pragma: no cover — defence-in-depth
        return None
    if redis is None:
        return None
    key = _RESEND_REDIS_KEY_PREFIX + _hash_email_for_throttle(email)
    try:
        ttl = await redis.ttl(key)
    except Exception:
        return None
    # redis-py returns -2 (key missing) or -1 (no TTL set) — both mean
    # "no throttle in flight". Otherwise the value IS the wait time.
    if ttl is None or ttl < 0:
        return 0
    return int(ttl)


async def record_magic_link_resend(email: str) -> None:
    """Stamp the resend throttle so the next request within 60s short-circuits.

    Best-effort: Redis failures degrade silently. The DB-backed fallback
    in ``magic_link_resend_db_fallback_wait_seconds`` covers the missing
    Redis case via the token row's expiry timestamp.
    """
    from app.sessions.events import get_redis

    try:
        redis = await get_redis()
    except Exception:
        return
    if redis is None:
        return
    key = _RESEND_REDIS_KEY_PREFIX + _hash_email_for_throttle(email)
    try:
        await redis.set(key, "1", ex=MAGIC_LINK_RESEND_WINDOW_SECONDS)
    except Exception:
        # Throttle storage is advisory — the DB fallback still rejects
        # within the same window via the token row.
        return


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


async def _pending_email_owner(db: AsyncSession, email: str) -> uuid.UUID | None:
    """Return the ``users.id`` that currently reserves ``email`` as a pending change.

    Case-insensitive lookup (CITEXT on Postgres, ``lower()`` on SQLite via
    the test harness). Returns ``None`` when no in-flight email change has
    claimed this address — that's the common case and lets the caller fall
    through to the standard upsert path.
    """
    row = (
        await db.execute(select(User.id).where(sa_func.lower(User.pending_email) == email.lower()))
    ).scalar_one_or_none()
    return row


async def create_magic_link(
    db: AsyncSession,
    email: str,
    base_url: str,
    *,
    next_path: str | None = None,
) -> str | None:
    """Upsert the user by email, store a hashed token, return the magic link URL.

    The link is valid for ``settings.magic_link_ttl_minutes`` minutes (default 30).
    Before issuing a new token we revoke all prior unconsumed tokens for the
    user — this means an attacker who steals an old (but still unexpired)
    email cannot replay it once the user has requested a fresh link.

    ``next_path`` (Phase 4.A.13) — optional same-origin relative path the
    callback redirects to after minting the session cookie. Persisted on
    the token row; re-validated against the FE-route allowlist on read
    (so a stale path minted under an older allowlist gets sanitised).
    NULL means "use the default ``/missions``".

    Returns ``None`` when the requested email is currently reserved as
    ``pending_email`` on another account (a P0-6 in-flight email change).
    The caller is expected to honor the standard 204-always convention
    when forwarding this result on ``POST /auth/magic-link`` — see the
    "reverse-direction TOCTOU" audit note in routes.py. We deliberately
    do NOT create a new user row for the requested email in that window
    because the in-flight change would otherwise fail with a UNIQUE
    integrity error on confirm, surfacing as a 500.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    # Phase 4.A.12 — normalize at the boundary so the SQLite test path
    # (which stores raw strings) and the Postgres CITEXT prod path land
    # the same canonical value. The route already lower-cases before
    # calling this, but we re-normalize defensively for direct callers
    # (e.g. test fixtures).
    email = email.strip().lower()

    # Upsert user by email.
    existing_user: User | None = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if existing_user is None:
        # Reverse-direction TOCTOU defence (P0-6 audit). If the address is
        # already claimed as ``pending_email`` on some OTHER account, refuse
        # to mint a fresh sign-up here — creating a row now would race with
        # the other account's ``POST /me/email/confirm`` and either steal
        # the address from the in-flight confirm or surface a 500. Until
        # that pending change either lands or expires, the address is
        # treated as reserved. Callers up-stack convert this into the
        # privacy-preserving 204 (no information leak about whether the
        # address is in-flight or simply unsent).
        pending_owner_id: uuid.UUID | None = await _pending_email_owner(db, email)
        if pending_owner_id is not None:
            from loguru import logger as _logger

            from app.observability import magic_link_suppressed_total

            _logger.info(
                "magic_link.suppressed reason=pending_email_in_flight owner_user_id={}",
                pending_owner_id,
            )
            magic_link_suppressed_total.labels(reason="pending_email_in_flight").inc()
            return None

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

    # Revoke prior unconsumed sign-in tokens so a fresh link supersedes any
    # in-flight one. Email-change tokens (a separate purpose) are NOT
    # invalidated here — a user signing in mid-email-change must still be
    # able to confirm the address change with the link already in their
    # other inbox.
    await db.execute(
        update(MagicLinkToken)
        .where(
            MagicLinkToken.user_id == user.id,
            MagicLinkToken.used_at.is_(None),
            MagicLinkToken.purpose == PURPOSE_SIGN_IN,
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
        purpose=PURPOSE_SIGN_IN,
        # Phase 4.A.13 — persist the validated next_path so the callback
        # can read it back without round-tripping the value through the
        # URL (a query param would be tamper-able by anyone who steals
        # the link).
        next_path=next_path,
    )
    db.add(magic)
    await db.flush()

    return f"{base_url}/auth/callback?token={raw_token}"


async def create_email_change_link(
    db: AsyncSession,
    *,
    user: User,
    new_email: str,
    base_url: str,
) -> str:
    """Mint a P0-6 email-change magic link bound to ``user`` and the new address.

    Caller responsibilities:

    * Validate the new address shape + uniqueness BEFORE calling this.
    * Set ``user.pending_email = new_email`` in the same transaction.

    The token stored here is purpose=``email_change`` so the sign-in flow
    can never accept it (and vice versa). On consume, the confirm route
    cross-checks the token's ``user_id`` against ``current_user.id`` AND
    asserts the stored ``pending_email`` still equals ``new_email``.

    We deliberately do NOT revoke prior email-change tokens for the same
    user — the user might have re-requested with a different target
    address; revoking only the in-flight sign_in tokens (above) keeps the
    blast radius narrow. Each email_change request bumps ``pending_email``
    on the user row, which is the effective revocation.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    # Invalidate any prior unconsumed email-change tokens for this user so
    # a stale link to a previously-requested address can't be redeemed.
    await db.execute(
        update(MagicLinkToken)
        .where(
            MagicLinkToken.user_id == user.id,
            MagicLinkToken.used_at.is_(None),
            MagicLinkToken.purpose == PURPOSE_EMAIL_CHANGE,
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
        purpose=PURPOSE_EMAIL_CHANGE,
    )
    db.add(magic)
    await db.flush()
    # The web frontend's /auth/email-confirm page handles the email-change
    # redirect; the BE confirm endpoint accepts the raw token in a JSON body
    # so the FE can also POST it directly without a browser redirect. The
    # path MUST match apps/web/app/auth/email-confirm/page.tsx — an
    # earlier version pointed at /account/email/confirm, which 404'd in
    # the user's browser the moment they clicked the link.
    return f"{base_url.rstrip('/')}/auth/email-confirm?token={raw_token}"


async def consume_email_change_token(db: AsyncSession, raw_token: str) -> MagicLinkToken | None:
    """Consume a purpose=``email_change`` token. Returns the row or None.

    Returns ``None`` for unknown, already-used, expired, or wrong-purpose
    tokens. On success marks ``used_at`` so the same link cannot land the
    change twice. The caller is responsible for verifying ``token.user_id``
    against the currently-authenticated user and committing the row.

    Each early-return branch (P1-7) emits exactly one
    ``logger.warning`` plus a ``email_change_token_rejected_total{reason}``
    tick so ops can split benign replays (one user double-clicking) from
    suspicious patterns (cross-purpose token presented to the confirm
    endpoint, unknown tokens, expired tokens). The log uses an 8-char
    SHA-256 prefix of the token, not the token itself — non-reversible
    but stable enough to correlate two log lines about the same attempt.
    """
    from loguru import logger as _logger

    from app.auth.hashing import hash_token_for_log
    from app.observability import email_change_token_rejected_total

    token_hash = _hash_token(raw_token)
    now = datetime.now(UTC)
    token_prefix = hash_token_for_log(raw_token)

    row: MagicLinkToken | None = (
        await db.execute(select(MagicLinkToken).where(MagicLinkToken.token_hash == token_hash))
    ).scalar_one_or_none()

    if row is None:
        _logger.warning(
            "email_change_token.rejected reason=unknown token_prefix={}",
            token_prefix,
        )
        email_change_token_rejected_total.labels(reason="unknown").inc()
        return None

    if row.used_at is not None:
        _logger.warning(
            "email_change_token.rejected reason=already_used user_id={} token_prefix={} used_at={}",
            row.user_id,
            token_prefix,
            row.used_at.isoformat(),
        )
        email_change_token_rejected_total.labels(reason="already_used").inc()
        return None

    if row.purpose != PURPOSE_EMAIL_CHANGE:
        _logger.warning(
            "email_change_token.rejected reason=wrong_purpose "
            "user_id={} token_prefix={} purpose={}",
            row.user_id,
            token_prefix,
            row.purpose,
        )
        email_change_token_rejected_total.labels(reason="wrong_purpose").inc()
        return None

    # Re-check eligibility (expiry) in SQL so Postgres timestamptz vs
    # SQLite naive datetime comparisons stay correct.
    eligible: MagicLinkToken | None = (
        await db.execute(
            select(MagicLinkToken).where(
                MagicLinkToken.id == row.id,
                MagicLinkToken.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if eligible is None:
        _logger.warning(
            "email_change_token.rejected reason=expired user_id={} token_prefix={} expires_at={}",
            row.user_id,
            token_prefix,
            row.expires_at.isoformat() if row.expires_at else "<unknown>",
        )
        email_change_token_rejected_total.labels(reason="expired").inc()
        return None

    row.used_at = now
    db.add(row)
    await db.flush()
    return row


async def consume_magic_token(
    db: AsyncSession, raw_token: str
) -> tuple[User, str | None] | User | None:
    """Find a non-expired, unused ``sign_in`` token by hash; mark it used; return ``(user, next_path)``.

    Phase 4.A.13 — the return type widened from ``User | None`` to
    ``tuple[User, str | None] | None`` so the callback can read the
    persisted ``next_path`` without a second SELECT. Callers that only
    care about the user can unpack the tuple. The legacy ``User`` return
    form is preserved in the type union for back-compat with any test
    that monkeypatches this function with a User-returning stub.

    Returns ``None`` if the token is unknown, already used, expired, or
    minted for the wrong purpose (e.g. an ``email_change`` token presented
    to the sign-in callback). Filtering on ``purpose == PURPOSE_SIGN_IN`` is
    a hard auth-bypass mitigation: without it, a leaked email-change link
    (intended only to confirm an address) would be redeemable for a fresh
    session cookie. The sister :func:`consume_email_change_token` applies
    the symmetric filter.
    """
    token_hash = _hash_token(raw_token)
    now = datetime.now(UTC)

    # Look up by hash alone so we can distinguish "unknown token" from
    # "already-used token" — the latter is a replay signal worth logging.
    # Expiry is still checked in SQL via the eligibility query below so the
    # comparison stays inside the DB's timezone semantics (Postgres
    # ``timestamptz`` vs SQLite naive datetimes don't compare cleanly in
    # Python).
    row: MagicLinkToken | None = (
        await db.execute(select(MagicLinkToken).where(MagicLinkToken.token_hash == token_hash))
    ).scalar_one_or_none()

    if row is None:
        return None

    # Wrong-purpose tokens (e.g. an ``email_change`` token presented to the
    # sign-in callback) must be rejected the same way an unknown token is —
    # do NOT mark them used, since the legitimate confirm flow still needs
    # to consume them on its own endpoint. Returning ``None`` here keeps
    # the eligibility check below from running against the wrong-purpose
    # row and prevents minting a session cookie.
    if row.purpose != PURPOSE_SIGN_IN:
        return None

    # Re-presenting a token that was already redeemed is suspicious — it
    # means either the user clicked the link twice from the same email or
    # someone intercepted the token. We can't tell the two apart but we can
    # surface the signal so incident response has a thread to pull.
    if row.used_at is not None:
        from loguru import logger as _logger

        _logger.warning(
            "magic_link.replay user_id={} token_used_at={}",
            row.user_id,
            row.used_at.isoformat(),
        )
        return None

    # Re-fetch with the expiry filter so SQL handles the timezone
    # comparison correctly (the row we already have may carry a naive
    # datetime when the backing DB is SQLite).
    eligible: MagicLinkToken | None = (
        await db.execute(
            select(MagicLinkToken).where(
                MagicLinkToken.id == row.id,
                MagicLinkToken.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if eligible is None:
        return None
    row = eligible

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
    if user is None:
        return None
    return user, row.next_path
