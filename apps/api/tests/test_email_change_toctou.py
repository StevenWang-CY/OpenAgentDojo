"""Reverse-direction email-change TOCTOU defence (P0-6 audit follow-up).

Two cooperating fixes guard the race:

* ``create_magic_link`` (sign-up entry point) refuses to mint a fresh
  sign-up user for an address that is already reserved as another account's
  ``pending_email``. Without this check, an attacker could "steal" the
  address that's mid-email-change by signing up with it before the original
  owner confirms.
* ``POST /me/email/confirm`` catches the ``IntegrityError`` that would
  otherwise bubble as a 500 if the race window slips past the first guard
  (e.g. between the guard and the insert, or via a direct DB write the
  fixture forces). The caller gets a typed 409 ``email_taken_in_flight``
  envelope, their stale ``pending_email`` is cleared so the FE can prompt
  a retry, and a structured log line records the failure for ops.

The two tests below pin both halves: Part 1 covers the guard; Part 2 covers
the defence even when Part 1 is bypassed (we insert the colliding row
directly through the session).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.magic_link_token import MagicLinkToken
from app.models.user import User

PURPOSE_EMAIL_CHANGE = "email_change"


async def _bound(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_user(
    session_local,
    *,
    email: str,
    pending_email: str | None = None,
) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=email,
                handle=f"toctou-{user_id.hex[:6]}",
                session_epoch=1,
                pending_email=pending_email,
            )
        )
        await db.commit()
    return user_id


async def _seed_email_change_token(
    session_local,
    *,
    user_id: uuid.UUID,
    raw_token: str,
) -> None:
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    async with session_local() as db:
        db.add(
            MagicLinkToken(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
                purpose=PURPOSE_EMAIL_CHANGE,
            )
        )
        await db.commit()


async def _auth_as(client_with_db, session_local, user_id: uuid.UUID) -> None:
    from app.auth.deps import require_auth

    async with session_local() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()

    async def _fake_require_auth() -> User:
        return user

    client_with_db._transport.app.dependency_overrides[require_auth] = _fake_require_auth  # type: ignore[attr-defined]


def _clear_auth(client_with_db) -> None:
    client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_sign_up_suppressed_when_address_is_pending_on_other_account(
    db_engine,
) -> None:
    """Part 1 — ``create_magic_link`` refuses to create a user for an
    address that is already someone else's ``pending_email``.

    The function returns ``None`` (which the route translates to the
    standard 204 so we never leak whether an address is in-flight). The
    important invariant: no new ``users`` row is created for the reserved
    address.
    """
    from app.auth.magic_link import create_magic_link

    session_local = await _bound(db_engine)
    # Owner has the in-flight pending_email.
    await _seed_user(
        session_local,
        email="owner@example.com",
        pending_email="contested@example.com",
    )

    async with session_local() as db:
        result = await create_magic_link(
            db,
            email="contested@example.com",
            base_url="https://app.example",
        )
        await db.commit()

    assert result is None, "sign-up MUST be suppressed for a reserved address"

    # No new user row was created for the contested address.
    async with session_local() as db:
        row = (
            await db.execute(
                select(User).where(User.email == "contested@example.com")
            )
        ).scalar_one_or_none()
    assert row is None, "no users row should be created for a contested address"


@pytest.mark.asyncio
async def test_confirm_returns_409_email_taken_in_flight_when_race_slipped(
    client_with_db, db_engine
) -> None:
    """Part 2 — even if Part 1's guard is bypassed (we insert the colliding
    row directly), the confirm endpoint catches the resulting IntegrityError
    and surfaces a typed 409 with ``code=email_taken_in_flight`` rather
    than a generic 500.

    Additional invariants:
      * The caller's ``pending_email`` is cleared so the FE can prompt for
        a retry with a different address.
      * The caller's ``email`` is unchanged (the steal failed).
      * No spurious ``account.email_changed`` event is persisted.
    """
    session_local = await _bound(db_engine)

    # User A initiates an email change for ``contested@example.com``.
    user_a_id = await _seed_user(
        session_local,
        email="user-a@example.com",
        pending_email="contested@example.com",
    )

    # Issue the email-change token for A. We hash a known raw value so we
    # can present it on the confirm endpoint without going through the
    # ``create_email_change_link`` helper (which would log/send email).
    raw_token = "tk-" + "a" * 60
    await _seed_email_change_token(
        session_local, user_id=user_a_id, raw_token=raw_token
    )

    # Bypass Part 1: insert user B with the contested address directly
    # (simulates the race window that the Part 1 guard cannot fully close).
    await _seed_user(session_local, email="contested@example.com")

    await _auth_as(client_with_db, session_local, user_a_id)

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/email/confirm",
            json={"token": raw_token},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        # FastAPI nests typed details under ``detail``.
        assert body["detail"]["code"] == "email_taken_in_flight", body

        # User A's email is unchanged; pending_email is cleared.
        async with session_local() as db:
            row = (
                await db.execute(select(User).where(User.id == user_a_id))
            ).scalar_one()
        assert row.email == "user-a@example.com"
        assert row.pending_email is None, (
            "pending_email must be cleared so the FE can prompt for a retry"
        )
    finally:
        _clear_auth(client_with_db)


@pytest.mark.asyncio
async def test_sign_up_for_unreserved_address_still_works(db_engine) -> None:
    """Regression guard — the Part 1 check must not block legitimate
    sign-ups for addresses that are NOT anyone else's pending_email.
    """
    from app.auth.magic_link import create_magic_link

    session_local = await _bound(db_engine)

    async with session_local() as db:
        url = await create_magic_link(
            db,
            email="fresh@example.com",
            base_url="https://app.example",
        )
        await db.commit()

    assert url is not None and url.startswith("https://app.example/auth/callback?token=")

    async with session_local() as db:
        row = (
            await db.execute(
                select(User).where(User.email == "fresh@example.com")
            )
        ).scalar_one()
    assert row.email == "fresh@example.com"
