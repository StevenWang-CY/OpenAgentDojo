"""P0-6 — two-step email-change flow.

* Happy path: change → confirm → user.email updated, pending_email cleared.
* Conflict: target email already on another account → 409.
* Invalid token: garbage token → 400.
* Idempotency: second confirm with the same (used) token → 400 (no double-apply).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.user import User


async def _bound(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_user(session_local, *, email: str | None = None) -> uuid.UUID:
    user_id = uuid.uuid4()
    email = email or f"ec-{user_id.hex[:8]}@test.local"
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=email,
                handle=f"ec-{user_id.hex[:6]}",
                session_epoch=1,
            )
        )
        await db.commit()
    return user_id


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
async def test_email_change_happy_path(client_with_db, db_engine, monkeypatch) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local, email="old@example.com")
    await _auth_as(client_with_db, session_local, user_id)

    # Capture the magic URL the email helper would send so we can extract
    # the raw token to feed into /me/email/confirm.
    sent_urls: list[str] = []

    async def _capture(to_email: str, magic_url: str, settings):
        sent_urls.append(magic_url)
        return True

    monkeypatch.setattr("app.auth.routes.send_email_change_link", _capture)

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/email/change",
            json={"new_email": "new@example.com"},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 204, resp.text
        assert len(sent_urls) == 1

        # pending_email landed on the user row.
        async with session_local() as db:
            row = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one()
        assert row.pending_email == "new@example.com"
        assert row.email == "old@example.com"

        # Extract the raw token from the magic URL.
        magic_url = sent_urls[0]
        assert "token=" in magic_url
        token = magic_url.rsplit("token=", 1)[-1]

        resp = await client_with_db.post(
            "/api/v1/auth/me/email/confirm",
            json={"token": token},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["email"] == "new@example.com"
        assert body["pending_email"] is None

        async with session_local() as db:
            row = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one()
        assert row.email == "new@example.com"
        assert row.pending_email is None
        # The epoch must have rotated as part of the confirm.
        assert row.session_epoch == 2

        # Idempotency: re-presenting the now-used token must NOT relog.
        resp = await client_with_db.post(
            "/api/v1/auth/me/email/confirm",
            json={"token": token},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["code"] == "invalid_token"
    finally:
        _clear_auth(client_with_db)


@pytest.mark.asyncio
async def test_email_change_rejects_when_target_in_use(
    client_with_db, db_engine, monkeypatch
) -> None:
    session_local = await _bound(db_engine)
    me_id = await _seed_user(session_local, email="me@example.com")
    # Another account already owns the target address.
    await _seed_user(session_local, email="other@example.com")
    await _auth_as(client_with_db, session_local, me_id)

    sent_urls: list[str] = []

    async def _capture(to_email: str, magic_url: str, settings):
        sent_urls.append(magic_url)
        return True

    monkeypatch.setattr("app.auth.routes.send_email_change_link", _capture)

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/email/change",
            json={"new_email": "other@example.com"},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "email_in_use"
        # No email should have been dispatched for a rejected change.
        assert sent_urls == []
    finally:
        _clear_auth(client_with_db)


@pytest.mark.asyncio
async def test_email_confirm_rejects_garbage_token(client_with_db, db_engine) -> None:
    session_local = await _bound(db_engine)
    me_id = await _seed_user(session_local, email="me@example.com")
    await _auth_as(client_with_db, session_local, me_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/email/confirm",
            json={"token": "not-a-real-token-but-long-enough-to-pass"},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "invalid_token"
    finally:
        _clear_auth(client_with_db)


@pytest.mark.asyncio
async def test_email_change_rejects_unchanged_address(client_with_db, db_engine) -> None:
    session_local = await _bound(db_engine)
    me_id = await _seed_user(session_local, email="me@example.com")
    await _auth_as(client_with_db, session_local, me_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/email/change",
            json={"new_email": "me@example.com"},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "email_unchanged"
    finally:
        _clear_auth(client_with_db)


def _ts_now() -> datetime:
    return datetime.now(UTC)
