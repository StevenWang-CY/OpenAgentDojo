"""P0-6 — deletion grace timer + the hard-delete worker.

Covers:

* POST /me/delete sets ``deletion_scheduled_at`` and emails the cancel link.
* POST /me/delete/cancel clears the timer.
* While scheduled, mutating endpoints are 403'd by the lockout middleware
  (covered more exhaustively in test_deletion_lock_middleware.py — this
  module exercises the schedule/cancel state machine).
* Cancelling AFTER the grace has elapsed returns 410.
* Running ``process_deletion_grace()`` after the grace expires tombstones
  the account and cascades the per-user rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User


async def _bound(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_user(session_local, *, email: str | None = None) -> uuid.UUID:
    user_id = uuid.uuid4()
    email = email or f"del-{user_id.hex[:8]}@test.local"
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=email,
                handle=f"del-{user_id.hex[:6]}",
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
async def test_schedule_then_cancel(client_with_db, db_engine, monkeypatch) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local, email="me@example.com")
    await _auth_as(client_with_db, session_local, user_id)

    async def _capture(**kwargs):
        return True

    monkeypatch.setattr("app.auth.routes.send_deletion_scheduled_email", _capture)

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/delete",
            json={"confirm_email": "me@example.com"},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 200, resp.text
        scheduled_for = resp.json()["scheduled_for"]
        assert scheduled_for

        async with session_local() as db:
            row = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one()
        assert row.deletion_scheduled_at is not None
        # Epoch must have rotated alongside the schedule.
        assert row.session_epoch == 2

        # Cancel — must succeed because deletion_scheduled_at is in the future.
        resp = await client_with_db.post(
            "/api/v1/auth/me/delete/cancel",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 204, resp.text

        async with session_local() as db:
            row = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one()
        assert row.deletion_scheduled_at is None
    finally:
        _clear_auth(client_with_db)


@pytest.mark.asyncio
async def test_delete_rejects_wrong_confirm_email(
    client_with_db, db_engine, monkeypatch
) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local, email="me@example.com")
    await _auth_as(client_with_db, session_local, user_id)

    async def _capture(**kwargs):
        return True

    monkeypatch.setattr("app.auth.routes.send_deletion_scheduled_email", _capture)

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/delete",
            json={"confirm_email": "wrong@example.com"},
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "email_mismatch"
    finally:
        _clear_auth(client_with_db)


@pytest.mark.asyncio
async def test_cancel_returns_410_after_grace_expired(
    client_with_db, db_engine
) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)
    # Manually set the deletion to a past timestamp — simulates the
    # grace having elapsed without re-running the schedule endpoint.
    async with session_local() as db:
        row = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        row.deletion_scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()

    await _auth_as(client_with_db, session_local, user_id)

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/delete/cancel",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 410, resp.text
        assert resp.json()["detail"]["code"] == "deletion_already_processed"
    finally:
        _clear_auth(client_with_db)


@pytest.mark.asyncio
async def test_process_deletion_grace_tombstones_account(db_engine) -> None:
    """Fast-forward time by setting deletion_scheduled_at into the past,
    then run the worker. The user row must end up tombstoned and child
    rows (sessions) must be deleted."""
    session_local = await _bound(db_engine)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email="goner@example.com",
                handle="goner",
                session_epoch=1,
                deletion_scheduled_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        db.add(
            Mission(
                id="del-mission",
                title="Del",
                difficulty="beginner",
                category="auth",
                repo_pack="x",
                initial_commit="HEAD",
                estimated_minutes=5,
                failure_mode="x",
                skills_tested=["x"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="del-mission",
                status="active",
                started_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
        await db.commit()

    # Point the worker at the test SQLite engine.
    from app.db import session as session_module

    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = session_local  # type: ignore[assignment]
    try:
        # The public ``process_deletion_grace`` wrapper calls
        # ``asyncio.run`` which cannot nest inside pytest-asyncio's loop;
        # the async coroutine is the public contract under test, the sync
        # wrapper is the cron entrypoint.
        from app.workers.account_deletion import _async_process_deletion_grace

        processed = await _async_process_deletion_grace()
    finally:
        session_module.AsyncSessionLocal = original  # type: ignore[assignment]

    assert processed == 1

    async with session_local() as db:
        row = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one()
    # Tombstone shape: deleted-{8hex}@deleted.openagentdojo.app
    assert row.email.startswith("deleted-")
    assert row.email.endswith("@deleted.openagentdojo.app")
    assert row.handle.startswith("deleted-")
    assert row.display_name is None
    assert row.deletion_scheduled_at is None
    assert row.session_epoch == 2  # bumped by the worker

    # The session row must be gone (per-user cascade).
    async with session_local() as db:
        sessions = (
            await db.execute(select(SessionRow).where(SessionRow.user_id == user_id))
        ).scalars().all()
    assert sessions == []


@pytest.mark.asyncio
async def test_cancel_when_nothing_scheduled_is_idempotent(
    client_with_db, db_engine
) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)
    await _auth_as(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/delete/cancel",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 204
    finally:
        _clear_auth(client_with_db)
