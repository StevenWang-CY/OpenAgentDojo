"""P0-6 — DeletionLockMiddleware blocks mutating endpoints during grace.

Every POST/PUT/PATCH/DELETE returns 403 with ``code='deletion_scheduled'``
EXCEPT ``/me/delete/cancel`` (the explicit escape hatch). GET requests
are unaffected.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.session_cookie import (
    _mark_revoked,
    clear_revoked_jtis,
    issue_session_cookie,
)
from app.config import get_settings
from app.db.base import Base
from app.models.user import User


class _Capture:
    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def set_cookie(self, *, key: str, value: str, **_kwargs: object) -> None:
        self.cookies[key] = value


async def _bound(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_scheduled_user(session_local) -> tuple[uuid.UUID, str]:
    """Seed a user with deletion_scheduled_at set in the future + return id+cookie."""
    user_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=f"lock-{user_id.hex[:8]}@example.com",
                handle=f"lk-{user_id.hex[:6]}",
                deletion_scheduled_at=datetime.now(UTC) + timedelta(days=3),
                session_epoch=1,
            )
        )
        await db.commit()

    settings = get_settings()
    cap = _Capture()
    issue_session_cookie(cap, str(user_id), settings, epoch=1)
    return user_id, cap.cookies[settings.session_cookie_name]


@pytest.mark.asyncio
async def test_lockout_returns_403_for_post(client_with_db, db_engine) -> None:
    session_local = await _bound(db_engine)
    user_id, cookie = await _seed_scheduled_user(session_local)

    settings = get_settings()
    client_with_db.cookies.set(settings.session_cookie_name, cookie)
    client_with_db.cookies.set("arena_csrf", "tok")

    # Tutorial replay is a POST we own; should be blocked.
    resp = await client_with_db.post(
        "/api/v1/auth/me/tutorial/replay",
        headers={"X-Csrf-Token": "tok"},
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "deletion_scheduled"
    assert body["scheduled_for"]


@pytest.mark.asyncio
async def test_lockout_returns_403_for_patch(client_with_db, db_engine) -> None:
    session_local = await _bound(db_engine)
    user_id, cookie = await _seed_scheduled_user(session_local)

    settings = get_settings()
    client_with_db.cookies.set(settings.session_cookie_name, cookie)
    client_with_db.cookies.set("arena_csrf", "tok")

    resp = await client_with_db.patch(
        "/api/v1/auth/me",
        json={"display_name": "New"},
        headers={"X-Csrf-Token": "tok"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "deletion_scheduled"


@pytest.mark.asyncio
async def test_cancel_is_exempt_from_lockout(client_with_db, db_engine) -> None:
    """The cancel endpoint must NOT be 403'd — otherwise users couldn't escape."""
    session_local = await _bound(db_engine)
    user_id, cookie = await _seed_scheduled_user(session_local)

    settings = get_settings()
    client_with_db.cookies.set(settings.session_cookie_name, cookie)
    client_with_db.cookies.set("arena_csrf", "tok")

    resp = await client_with_db.post(
        "/api/v1/auth/me/delete/cancel",
        headers={"X-Csrf-Token": "tok"},
    )
    assert resp.status_code == 204, resp.text

    # Confirm the row is no longer scheduled.
    async with session_local() as db:
        row = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert row.deletion_scheduled_at is None


@pytest.mark.asyncio
async def test_get_requests_pass_through_lockout(client_with_db, db_engine) -> None:
    """GET endpoints stay readable so the user can confirm the schedule + cancel."""
    session_local = await _bound(db_engine)
    user_id, cookie = await _seed_scheduled_user(session_local)

    settings = get_settings()
    client_with_db.cookies.set(settings.session_cookie_name, cookie)

    resp = await client_with_db.get("/api/v1/auth/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deletion_scheduled_at"] is not None


@pytest.mark.asyncio
async def test_revoked_cookie_does_not_leak_deletion_state(client_with_db, db_engine) -> None:
    """A logged-out (jti-revoked) cookie must NOT trigger the 403 lockout body.

    ``revoke_session_cookie`` (logout) marks the jti revoked but does NOT
    bump ``session_epoch``, so a logged-out-but-unexpired cookie still
    passes the epoch check. Without the revocation short-circuit the
    middleware would emit ``code='deletion_scheduled'`` (incl
    ``scheduled_for``), leaking the deletion-grace timestamp to a
    terminated session. The request must instead fall through to route
    auth (401) and never carry the deletion code.
    """
    from jose import jwt

    clear_revoked_jtis()
    try:
        session_local = await _bound(db_engine)
        _user_id, cookie = await _seed_scheduled_user(session_local)

        settings = get_settings()
        # Revoke this cookie's jti WITHOUT bumping the user's epoch —
        # exactly the state logout leaves behind.
        payload = jwt.decode(cookie, settings.session_secret, algorithms=["HS256"])
        _mark_revoked(payload["jti"])

        client_with_db.cookies.set(settings.session_cookie_name, cookie)
        client_with_db.cookies.set("arena_csrf", "tok")

        resp = await client_with_db.post(
            "/api/v1/auth/me/tutorial/replay",
            headers={"X-Csrf-Token": "tok"},
        )
        # Fall-through to route auth — a revoked cookie is unauthenticated.
        assert resp.status_code == 401, resp.text
        # The body MUST NOT leak the deletion grace timestamp.
        assert resp.json().get("code") != "deletion_scheduled"
        assert "scheduled_for" not in resp.json()
    finally:
        clear_revoked_jtis()


@pytest.mark.asyncio
async def test_lockout_does_not_block_unauthenticated_requests(client_with_db, db_engine) -> None:
    """No cookie → middleware short-circuits and the auth layer handles it."""
    session_local = await _bound(db_engine)
    await _seed_scheduled_user(session_local)

    client_with_db.cookies.set("arena_csrf", "tok")
    # No session cookie → DeletionLockMiddleware can't identify a user;
    # auth dependency on the (POST) route will 401, NOT 403.
    resp = await client_with_db.post(
        "/api/v1/auth/me/tutorial/replay",
        headers={"X-Csrf-Token": "tok"},
    )
    # The exact status depends on which auth gate runs first; either way
    # the body MUST NOT carry the deletion_scheduled code (which would
    # leak account state to anonymous callers).
    assert resp.status_code in (401, 403)
    if resp.status_code == 403:
        assert resp.json().get("code") != "deletion_scheduled"
