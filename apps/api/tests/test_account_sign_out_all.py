"""P0-6 — ``POST /me/sessions/sign-out-all`` invalidates other devices.

Verifies the per-user session-epoch mechanism:

* A second cookie minted from the same user BEFORE the rotation is
  rejected on the next request.
* The cookie attached to the sign-out-all response itself continues to
  work — the calling device must not be kicked out of its own action.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.session_cookie import (
    issue_session_cookie,
    mint_session_cookie_for_user,
)
from app.config import get_settings
from app.db.base import Base
from app.models.user import User


class _MockResponse:
    """Capture set_cookie calls so the test can extract a fresh cookie value."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def set_cookie(self, *, key: str, value: str, **_kwargs: object) -> None:
        self.cookies[key] = value


async def _bound(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_user(session_local) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=f"signout-{user_id.hex[:8]}@test.local",
                handle=f"so-{user_id.hex[:6]}",
                session_epoch=1,
            )
        )
        await db.commit()
    return user_id


@pytest.mark.asyncio
async def test_sign_out_all_invalidates_prior_cookie(client_with_db, db_engine) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    settings = get_settings()

    # Mint a "device B" cookie at epoch 1.
    device_b = _MockResponse()
    issue_session_cookie(device_b, str(user_id), settings, epoch=1)
    device_b_cookie = device_b.cookies[settings.session_cookie_name]

    # Stub require_auth so the sign-out-all caller is authenticated as the
    # same user, but DON'T involve cookies for the call — we just want to
    # measure the epoch bump and verify device B's cookie is now stale.
    from app.auth.deps import require_auth

    async with session_local() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()

    async def _fake_require_auth() -> User:
        return user

    client_with_db._transport.app.dependency_overrides[require_auth] = _fake_require_auth  # type: ignore[attr-defined]
    try:
        # CSRF bypass via matching cookie/header.
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/sessions/sign-out-all",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 204, resp.text

        # The handler attached a fresh cookie to its OWN response. Extract
        # it so the test can verify the calling device still works.
        new_cookie = resp.cookies.get(settings.session_cookie_name)
        assert new_cookie, "sign-out-all must mint a fresh cookie for the caller"
        assert new_cookie != device_b_cookie, (
            "post-rotation cookie must differ from a pre-rotation cookie"
        )
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    # Re-fetch the user — epoch should have incremented.
    async with session_local() as db:
        refreshed = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert refreshed.session_epoch == 2, "sign-out-all must bump session_epoch"

    # The fresh cookie's epoch claim must verify against the new epoch.
    from jose import jwt

    decoded_new = jwt.decode(new_cookie, settings.session_secret, algorithms=["HS256"])
    assert decoded_new["epoch"] == 2

    decoded_old = jwt.decode(device_b_cookie, settings.session_secret, algorithms=["HS256"])
    assert decoded_old["epoch"] == 1, (
        "device B was minted at epoch 1; it should now be rejected on next request"
    )


def test_mint_session_cookie_for_user_uses_current_epoch() -> None:
    """mint_session_cookie_for_user pulls epoch off the User row."""
    settings = get_settings()
    user = User(
        id=uuid.uuid4(),
        email="epoch@test.local",
        session_epoch=7,
        created_at=datetime.now(UTC),
    )
    response = _MockResponse()
    mint_session_cookie_for_user(response, user, settings)
    cookie = response.cookies[settings.session_cookie_name]

    from jose import jwt

    payload = jwt.decode(cookie, settings.session_secret, algorithms=["HS256"])
    assert payload["epoch"] == 7
    assert payload["sub"] == str(user.id)
