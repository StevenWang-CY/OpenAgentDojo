"""P0-2 — sign-out-everywhere must kill already-issued WS tokens.

The audit found that ``verify_ws_token`` checked only the HMAC + session
id + expiry, so an already-issued WS token kept authenticating the
terminal after the user signed out of every device. Worse,
``refresh_ws_token`` would mint a perpetual successor against the same
secret. This test pins the fix: a token issued at epoch=1 stops
authenticating the moment ``users.session_epoch`` rotates, and a fresh
mint after the rotation is accepted.

The user-row lookup is exercised against the real SQLite engine so the
in-process epoch cache + DB fetch path both run.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.user import User
from app.ws import auth as ws_auth
from app.ws.auth import (
    WsTokenError,
    clear_epoch_cache,
    issue_ws_token,
    refresh_ws_token,
    verify_ws_token,
)

_SECRET = "test-secret-32-chars-min-aaaaaaaa"


@pytest_asyncio.fixture
async def session_local(db_engine):
    """Per-test session factory pointing at the in-memory SQLite engine."""
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _bind_async_session(db_engine, monkeypatch):
    """Rebind ``app.db.session.AsyncSessionLocal`` so the epoch lookup
    talks to the in-memory engine the fixture set up.

    Also clears the in-process epoch cache before AND after each test so
    a stale entry from a prior test never bleeds into the assertions.
    """
    from app.db import session as session_module

    bound = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", bound)
    clear_epoch_cache()
    yield
    clear_epoch_cache()


async def _seed_user(session_local, *, epoch: int = 1) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=f"ws-{user_id.hex[:8]}@test.local",
                handle=f"ws-{user_id.hex[:6]}",
                session_epoch=epoch,
            )
        )
        await db.commit()
    return user_id


async def _rotate_epoch(session_local, user_id: uuid.UUID, *, new: int) -> None:
    async with session_local() as db:
        await db.execute(update(User).where(User.id == user_id).values(session_epoch=new))
        await db.commit()


@pytest.mark.asyncio
async def test_verify_ws_token_rejects_stale_epoch(session_local) -> None:
    """Token minted at epoch=1 fails verify once the row rotates to epoch=2."""
    user_id = await _seed_user(session_local, epoch=1)
    sid = str(uuid.uuid4())

    token = issue_ws_token(sid, user_id=str(user_id), epoch=1, secret=_SECRET)
    # Initially valid — current epoch matches the claim.
    assert verify_ws_token(token, sid, secret=_SECRET) is True

    # Sign-out-everywhere (or email-change / deletion-schedule) rotates
    # the epoch. The in-process cache held the old value; flush it so the
    # next verify re-reads the DB. In production the cache TTL is 5s —
    # explicit clear here keeps the test deterministic without sleeping.
    await _rotate_epoch(session_local, user_id, new=2)
    clear_epoch_cache()

    assert verify_ws_token(token, sid, secret=_SECRET) is False


@pytest.mark.asyncio
async def test_fresh_token_after_rotation_is_accepted(session_local) -> None:
    """A token minted at the NEW epoch authenticates fine."""
    user_id = await _seed_user(session_local, epoch=1)
    sid = str(uuid.uuid4())

    await _rotate_epoch(session_local, user_id, new=2)
    clear_epoch_cache()

    fresh = issue_ws_token(sid, user_id=str(user_id), epoch=2, secret=_SECRET)
    assert verify_ws_token(fresh, sid, secret=_SECRET) is True


@pytest.mark.asyncio
async def test_refresh_re_issues_with_current_epoch(session_local) -> None:
    """``refresh_ws_token`` MUST rebind to the latest epoch, not the stale claim."""
    user_id = await _seed_user(session_local, epoch=1)
    sid = str(uuid.uuid4())

    token = issue_ws_token(sid, user_id=str(user_id), epoch=1, secret=_SECRET)

    # Rotate AFTER mint; refresh must refuse (claim < current).
    await _rotate_epoch(session_local, user_id, new=2)
    clear_epoch_cache()
    with pytest.raises(WsTokenError):
        refresh_ws_token(token, sid, secret=_SECRET)

    # Re-issue a fresh token at the current epoch — refresh now succeeds
    # and the returned token verifies cleanly.
    new_token = issue_ws_token(sid, user_id=str(user_id), epoch=2, secret=_SECRET)
    refreshed = refresh_ws_token(new_token, sid, secret=_SECRET)
    assert verify_ws_token(refreshed, sid, secret=_SECRET) is True


@pytest.mark.asyncio
async def test_verify_rejects_when_user_row_missing(session_local) -> None:
    """Deleted accounts cannot authenticate even with a still-fresh signature."""
    user_id = uuid.uuid4()  # never inserted
    sid = str(uuid.uuid4())
    token = issue_ws_token(sid, user_id=str(user_id), epoch=1, secret=_SECRET)
    # Force a miss so the in-process cache from any leak-through test is empty.
    clear_epoch_cache()
    assert verify_ws_token(token, sid, secret=_SECRET) is False


@pytest.mark.asyncio
async def test_v0_legacy_token_rejected(monkeypatch) -> None:
    """Pre-P0-2 tokens (no version prefix, no user/epoch claim) are refused.

    Belt-and-braces — every issuer path goes through the v1 builder now,
    but we want a forged v0 token (or one minted by a stale replica during
    a deploy) to fail closed.
    """
    import base64
    import hmac
    import time
    from hashlib import sha256

    sid = str(uuid.uuid4())
    legacy_payload = f"{sid}:{int(time.time()) + 60}".encode()
    mac = hmac.new(_SECRET.encode(), legacy_payload, sha256).digest()
    payload_b64 = base64.urlsafe_b64encode(legacy_payload).rstrip(b"=").decode()
    mac_b64 = base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    legacy_token = f"{payload_b64}.{mac_b64}"

    # Spy on _load_current_epoch to confirm the version gate fires before
    # any DB lookup. monkeypatch reverts the patch automatically at teardown.
    called: dict[str, int] = {}

    def _spy(uid: str) -> int:
        called["count"] = called.get("count", 0) + 1
        return 1

    monkeypatch.setattr(ws_auth, "_load_current_epoch", _spy)
    assert verify_ws_token(legacy_token, sid, secret=_SECRET) is False
    assert called.get("count", 0) == 0, "version gate must reject v0 tokens before any DB lookup"
