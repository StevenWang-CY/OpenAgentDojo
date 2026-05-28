"""``GET /me/data-export/latest`` lets the FE adopt an existing export on mount.

Without this endpoint the panel's "active export" state lived only in
local React state, seeded by a successful POST. A page reload dropped
that state, so the empty "No exports yet" copy rendered even when a real
queued/running DB row existed — then POST hit the in-flight unique index
and surfaced "Another export is in flight." Two contradictory messages
on the same panel.

Contract pinned here:

* 204 when the user has never exported.
* 200 with the most-recent envelope when at least one export exists,
  regardless of status (queued / running / ready / failed / expired).
  ``requested_at desc`` is the load-bearing ordering.
* The owner gate uses the standard ``require_auth`` dep — cross-user
  reads are impossible because the WHERE clause includes ``user_id ==
  caller.id``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.data_export import (
    EXPORT_STATUS_FAILED,
    EXPORT_STATUS_QUEUED,
    EXPORT_STATUS_READY,
    EXPORT_STATUS_RUNNING,
    DataExport,
)
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
                email=f"latest-{user_id.hex[:8]}@example.com",
                handle=f"la-{user_id.hex[:6]}",
                session_epoch=1,
            )
        )
        await db.commit()
    return user_id


async def _with_auth(client_with_db, session_local, user_id):
    from app.auth.deps import require_auth

    async with session_local() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()

    async def _fake_auth() -> User:
        return user

    client_with_db._transport.app.dependency_overrides[require_auth] = _fake_auth  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_latest_returns_204_for_user_with_no_exports(client_with_db, db_engine) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)
    await _with_auth(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.get(
            "/api/v1/auth/me/data-export/latest",
            headers={"X-Csrf-Token": "tok"},
        )
        # 204 means "no exports yet" → FE renders the empty CTA. The body
        # must be empty so the FE's request<T> helper returns undefined.
        assert resp.status_code == 204, resp.text
        assert resp.content in (b"", b"null")
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_latest_adopts_queued_row(client_with_db, db_engine) -> None:
    """The bug-fix scenario: the user has a queued row from a previous
    session (or our sweep is about to rescue it). ``/latest`` returns it
    so the panel mounts in the polling state instead of the empty CTA."""
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    export_id = uuid.uuid4()
    async with session_local() as db:
        db.add(DataExport(id=export_id, user_id=user_id, status=EXPORT_STATUS_QUEUED))
        await db.commit()

    await _with_auth(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.get(
            "/api/v1/auth/me/data-export/latest",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == str(export_id)
        assert body["status"] == EXPORT_STATUS_QUEUED
        assert body["download_url"] is None
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_latest_orders_by_requested_at_desc(client_with_db, db_engine) -> None:
    """Multiple historical exports: ``/latest`` returns the newest,
    regardless of status."""
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    older_id = uuid.uuid4()
    newer_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with session_local() as db:
        older = DataExport(
            id=older_id,
            user_id=user_id,
            status=EXPORT_STATUS_READY,
            s3_key="x",
            bytes_total=1,
            ready_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=5),
        )
        newer = DataExport(
            id=newer_id,
            user_id=user_id,
            status=EXPORT_STATUS_FAILED,
            error="prior_failure",
        )
        db.add_all([older, newer])
        await db.flush()
        older.requested_at = now - timedelta(days=2)
        newer.requested_at = now - timedelta(minutes=5)
        await db.commit()

    await _with_auth(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.get(
            "/api/v1/auth/me/data-export/latest",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Newer row is failed — surface it. The panel renders the "Try
        # again" CTA because failed isn't terminal-for-blocking-purposes.
        assert body["id"] == str(newer_id)
        assert body["status"] == EXPORT_STATUS_FAILED
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_latest_is_per_user(client_with_db, db_engine) -> None:
    """A user MUST NOT see another user's most-recent export."""
    session_local = await _bound(db_engine)
    user_a = await _seed_user(session_local)
    user_b = await _seed_user(session_local)

    async with session_local() as db:
        db.add(DataExport(user_id=user_a, status=EXPORT_STATUS_RUNNING))
        await db.commit()

    await _with_auth(client_with_db, session_local, user_b)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.get(
            "/api/v1/auth/me/data-export/latest",
            headers={"X-Csrf-Token": "tok"},
        )
        # B has no exports — must get 204 even though A has one.
        assert resp.status_code == 204, resp.text
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_latest_lazily_expires_ready_past_due(client_with_db, db_engine) -> None:
    """A ready row whose expires_at is in the past flips to ``expired``
    in the same response — matches the per-id endpoint's lazy expiry."""
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    export_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            DataExport(
                id=export_id,
                user_id=user_id,
                status=EXPORT_STATUS_READY,
                s3_key="k",
                bytes_total=1,
                ready_at=datetime.now(UTC) - timedelta(days=10),
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await db.commit()

    await _with_auth(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.get(
            "/api/v1/auth/me/data-export/latest",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "expired"
        assert body["download_url"] is None
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]
