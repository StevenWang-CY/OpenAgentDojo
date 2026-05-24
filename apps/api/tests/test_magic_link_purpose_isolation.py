"""Gap 1 — magic-link purpose isolation between sign-in and email-change.

Before this regression test landed, ``consume_magic_token`` (the
``/auth/callback`` consumer) selected any unused, unexpired token by
hash — including ``purpose='email_change'`` rows. That meant any
``email_change`` link mailed to a user's NEW address could be redeemed
on ``/auth/callback`` for a fresh sign-in cookie, bypassing the intent
of the email-change two-step flow.

The symmetric direction was already correct (``consume_email_change_token``
filters on ``purpose='email_change'``), but the absence of a mirror
filter on the sign-in side made the auth bypass trivial — anyone with
access to a leaked email-change link could elevate it to a session.

These tests pin both directions:

  1. An ``email_change`` token presented to ``/auth/callback`` is rejected.
  2. The same token is NOT marked ``used_at`` by that rejection — so the
     legitimate ``/auth/me/email/confirm`` flow can still consume it on
     its dedicated endpoint.
  3. Calling ``/auth/me/email/confirm`` with the same token then succeeds
     (the row was preserved, not invalidated).
  4. Mirror: a ``sign_in`` token presented to ``/auth/me/email/confirm``
     is rejected.

The tests drive ``consume_magic_token`` + ``consume_email_change_token``
directly because the route-level callback also redirects on the success
path (which complicates the assertion); the direct unit-level call
exercises the same code path the route delegates to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.auth.magic_link import (
    _hash_token,
    consume_email_change_token,
    consume_magic_token,
)
from app.models.magic_link_token import (
    PURPOSE_EMAIL_CHANGE,
    PURPOSE_SIGN_IN,
    MagicLinkToken,
)
from app.models.user import User


async def _seed_user(db_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"purpose-{uuid.uuid4().hex[:8]}@example.com",
        handle=f"p{uuid.uuid4().hex[:8]}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _mint_token(db_session, *, user: User, purpose: str) -> str:
    """Insert a fresh, unused, non-expired token of the given purpose."""
    raw = f"raw-{purpose}-{uuid.uuid4().hex}"
    token = MagicLinkToken(
        user_id=user.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        purpose=purpose,
    )
    db_session.add(token)
    await db_session.flush()
    return raw


@pytest.mark.asyncio
async def test_email_change_token_rejected_by_signin_consumer(db_session) -> None:
    """An ``email_change`` token MUST NOT mint a sign-in session.

    The consumer returns ``None`` (the route layer translates this to a
    400 / redirect to error) — and critically the row is left with
    ``used_at == None`` so the legitimate confirm flow can still use it.
    """
    user = await _seed_user(db_session)
    raw = await _mint_token(db_session, user=user, purpose=PURPOSE_EMAIL_CHANGE)

    result = await consume_magic_token(db_session, raw)
    assert result is None, (
        "consume_magic_token must reject email_change tokens; otherwise "
        "any leaked email-change link is redeemable for a session cookie"
    )

    # The row must NOT have been marked used — the legitimate confirm
    # flow still needs to redeem it.
    row = (
        await db_session.execute(
            select(MagicLinkToken).where(
                MagicLinkToken.token_hash == _hash_token(raw)
            )
        )
    ).scalar_one()
    assert row.used_at is None, (
        "wrong-purpose rejection must not consume the row — that would "
        "lock the user out of the legitimate confirm flow"
    )
    assert row.purpose == PURPOSE_EMAIL_CHANGE


@pytest.mark.asyncio
async def test_email_change_token_still_works_on_confirm_after_signin_reject(
    db_session,
) -> None:
    """After the sign-in consumer rejects, the confirm consumer succeeds."""
    user = await _seed_user(db_session)
    raw = await _mint_token(db_session, user=user, purpose=PURPOSE_EMAIL_CHANGE)

    # Sign-in path rejects (Gap 1 fix).
    assert await consume_magic_token(db_session, raw) is None

    # Confirm path still accepts the same token because the row was
    # preserved (not marked used) by the prior rejection.
    confirm_row = await consume_email_change_token(db_session, raw)
    assert confirm_row is not None, (
        "the legitimate confirm flow must still be able to redeem a "
        "token that was previously presented to the wrong endpoint"
    )
    assert confirm_row.user_id == user.id
    assert confirm_row.used_at is not None


@pytest.mark.asyncio
async def test_signin_token_rejected_by_email_change_consumer(db_session) -> None:
    """Mirror: a ``sign_in`` token cannot land an email change."""
    user = await _seed_user(db_session)
    raw = await _mint_token(db_session, user=user, purpose=PURPOSE_SIGN_IN)

    result = await consume_email_change_token(db_session, raw)
    assert result is None, (
        "consume_email_change_token must reject sign_in tokens"
    )

    # Sign-in row likewise stays unused so it can still be redeemed via
    # the sign-in callback.
    row = (
        await db_session.execute(
            select(MagicLinkToken).where(
                MagicLinkToken.token_hash == _hash_token(raw)
            )
        )
    ).scalar_one()
    assert row.used_at is None


@pytest.mark.asyncio
async def test_signin_token_consumed_by_signin_consumer_returns_user(
    db_session,
) -> None:
    """Sanity: a real sign_in token still works on the sign-in path."""
    user = await _seed_user(db_session)
    raw = await _mint_token(db_session, user=user, purpose=PURPOSE_SIGN_IN)

    returned = await consume_magic_token(db_session, raw)
    assert returned is not None
    assert returned.id == user.id

    row = (
        await db_session.execute(
            select(MagicLinkToken).where(
                MagicLinkToken.token_hash == _hash_token(raw)
            )
        )
    ).scalar_one()
    assert row.used_at is not None


# ---------------------------------------------------------------------------
# P1-7 — every consume_email_change_token rejection ticks a counter.
# ---------------------------------------------------------------------------


def _counter_value(reason: str) -> float:
    """Read the labelled value of email_change_token_rejected_total."""
    from app.observability import REGISTRY

    return REGISTRY.get_sample_value(
        "email_change_token_rejected_total",
        labels={"reason": reason},
    ) or 0.0


@pytest.mark.asyncio
async def test_unknown_token_increments_counter(db_session) -> None:
    before = _counter_value("unknown")
    result = await consume_email_change_token(db_session, "totally-bogus-token-value")
    assert result is None
    after = _counter_value("unknown")
    assert after == before + 1


@pytest.mark.asyncio
async def test_wrong_purpose_token_increments_counter(db_session) -> None:
    user = await _seed_user(db_session)
    raw = await _mint_token(db_session, user=user, purpose=PURPOSE_SIGN_IN)
    before = _counter_value("wrong_purpose")
    result = await consume_email_change_token(db_session, raw)
    assert result is None
    after = _counter_value("wrong_purpose")
    assert after == before + 1


@pytest.mark.asyncio
async def test_already_used_token_increments_counter(db_session) -> None:
    user = await _seed_user(db_session)
    raw = await _mint_token(db_session, user=user, purpose=PURPOSE_EMAIL_CHANGE)
    # First consume succeeds and marks the row used.
    first = await consume_email_change_token(db_session, raw)
    assert first is not None

    before = _counter_value("already_used")
    second = await consume_email_change_token(db_session, raw)
    assert second is None
    after = _counter_value("already_used")
    assert after == before + 1


@pytest.mark.asyncio
async def test_expired_token_increments_counter(db_session) -> None:
    user = await _seed_user(db_session)
    raw = f"raw-expired-{uuid.uuid4().hex}"
    token = MagicLinkToken(
        user_id=user.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
        purpose=PURPOSE_EMAIL_CHANGE,
    )
    db_session.add(token)
    await db_session.flush()

    before = _counter_value("expired")
    result = await consume_email_change_token(db_session, raw)
    assert result is None
    after = _counter_value("expired")
    assert after == before + 1
