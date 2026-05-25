"""P0-6 — POST /me/data-export rejects a second concurrent request.

The Postgres partial unique index on ``(user_id) WHERE status IN
('queued','running')`` is the production-tier guarantee; on SQLite (the
test harness) the route handler does its own pre-flight check. Both
return 409 ``code='export_in_flight'`` on conflict.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.data_export import DataExport
from app.models.user import User


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
                email=f"flight-{user_id.hex[:8]}@example.com",
                handle=f"fl-{user_id.hex[:6]}",
                session_epoch=1,
            )
        )
        await db.commit()
    return user_id


@pytest.mark.asyncio
async def test_second_export_returns_409(client_with_db, db_engine, monkeypatch) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    # Pre-seed an in-flight (running) row so the route's pre-flight check fires.
    existing_id = uuid.uuid4()
    async with session_local() as db:
        db.add(DataExport(id=existing_id, user_id=user_id, status="running"))
        await db.commit()

    from app.auth.deps import require_auth

    async with session_local() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()

    async def _fake_auth() -> User:
        return user

    client_with_db._transport.app.dependency_overrides[require_auth] = _fake_auth  # type: ignore[attr-defined]
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/data-export",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "export_in_flight"
        # The error references the existing export id so the FE can poll it.
        assert body["detail"]["export_id"] == str(existing_id)
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_second_export_after_completion_succeeds(
    client_with_db, db_engine, monkeypatch
) -> None:
    """A completed export must NOT block a fresh request."""
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    async with session_local() as db:
        db.add(
            DataExport(
                id=uuid.uuid4(),
                user_id=user_id,
                status="ready",
                s3_key="data-exports/x/y.zip",
                bytes_total=1234,
            )
        )
        await db.commit()

    # Inline-fallback path runs the worker; stub it so the test stays fast
    # and doesn't touch a real S3.
    async def _no_op_async(_export_id):
        return None

    def _no_op(_export_id):
        return None

    monkeypatch.setattr("app.workers.account_export.build_user_export", _no_op, raising=False)

    from app.auth.deps import require_auth

    async with session_local() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()

    async def _fake_auth() -> User:
        return user

    client_with_db._transport.app.dependency_overrides[require_auth] = _fake_auth  # type: ignore[attr-defined]
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/data-export",
            headers={"X-Csrf-Token": "tok"},
        )
        # 202 ACCEPTED on a fresh enqueue (the stub no-ops the worker,
        # so the row stays queued — the route returns it as-is).
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] in ("queued", "ready", "failed", "running")
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]
