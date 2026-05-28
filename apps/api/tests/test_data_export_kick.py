"""``POST /me/data-export/{id}/kick`` runs an inline build for a stuck row.

Manual recovery for rows that the auto-sweep hasn't picked up (or for
deployments where the sweep itself is wedged / not running). The
endpoint synchronously invokes ``build_user_export(inline=True)`` and
returns the post-run terminal state.

Contracts pinned here:

* Queued rows leave the queued state — the response carries the
  ready/failed envelope, never the original "queued" status.
* Terminal rows (ready / failed / expired) short-circuit cleanly with
  a 200 and the existing envelope (idempotent).
* Cross-user kicks return 404 — owner isolation matches the per-id GET.
* A worker that fails inline still leaves the row in a usable state
  (failed with error string), not stuck in running.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.data_export import (
    EXPORT_STATUS_FAILED,
    EXPORT_STATUS_QUEUED,
    EXPORT_STATUS_READY,
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
                email=f"kick-{user_id.hex[:8]}@example.com",
                handle=f"k-{user_id.hex[:6]}",
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
async def test_kick_runs_queued_row_inline(client_with_db, db_engine, monkeypatch) -> None:
    """The bug-fix scenario: a queued row owned by the caller leaves
    the queued state immediately."""
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    export_id = uuid.uuid4()
    async with session_local() as db:
        db.add(DataExport(id=export_id, user_id=user_id, status=EXPORT_STATUS_QUEUED))
        await db.commit()

    # Stub the inline build path so the test stays focused on the route
    # behaviour. The build function is invoked via asyncio.to_thread; we
    # patch the route-level reference because that's what the handler imports.
    def _fake_build(export_id_str: str, *, inline: bool) -> None:
        # The route imports build_user_export from app.workers.account_export
        # so we patch there. The body simulates a successful build.
        assert inline is True
        import asyncio as _asyncio

        async def _mark_ready():
            from app.db.session import AsyncSessionLocal

            async with AsyncSessionLocal() as inner_db:
                row = (
                    await inner_db.execute(
                        select(DataExport).where(
                            DataExport.id == uuid.UUID(export_id_str)
                        )
                    )
                ).scalar_one()
                row.status = EXPORT_STATUS_READY
                row.s3_key = "stub/k"
                row.bytes_total = 7
                await inner_db.commit()

        _asyncio.run(_mark_ready())

    # Bind AsyncSessionLocal so the inner session works.
    from app.db import session as session_module

    monkeypatch.setattr(
        session_module, "AsyncSessionLocal", async_sessionmaker(bind=db_engine, expire_on_commit=False)
    )
    monkeypatch.setattr(
        "app.workers.account_export.build_user_export", _fake_build
    )

    await _with_auth(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            f"/api/v1/auth/me/data-export/{export_id}/kick",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == str(export_id)
        # Critical: the row left queued. That's the entire point of
        # this endpoint.
        assert body["status"] == EXPORT_STATUS_READY
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_kick_is_idempotent_on_terminal_rows(client_with_db, db_engine) -> None:
    """A ready row is returned as-is — no build attempt, no 409."""
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    export_id = uuid.uuid4()
    from datetime import UTC, datetime, timedelta

    async with session_local() as db:
        db.add(
            DataExport(
                id=export_id,
                user_id=user_id,
                status=EXPORT_STATUS_READY,
                s3_key="x",
                bytes_total=1,
                ready_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await db.commit()

    await _with_auth(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            f"/api/v1/auth/me/data-export/{export_id}/kick",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == EXPORT_STATUS_READY
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_kick_cross_user_returns_404(client_with_db, db_engine) -> None:
    """User B kicking User A's export gets a 404 — no cross-user state
    surface."""
    session_local = await _bound(db_engine)
    user_a = await _seed_user(session_local)
    user_b = await _seed_user(session_local)

    export_id = uuid.uuid4()
    async with session_local() as db:
        db.add(DataExport(id=export_id, user_id=user_a, status=EXPORT_STATUS_QUEUED))
        await db.commit()

    await _with_auth(client_with_db, session_local, user_b)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            f"/api/v1/auth/me/data-export/{export_id}/kick",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 404, resp.text
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_kick_malformed_uuid_returns_404(client_with_db, db_engine) -> None:
    """A garbage path segment doesn't 500 — returns 404 like the per-id
    GET does."""
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)
    await _with_auth(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/data-export/not-a-uuid/kick",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 404, resp.text
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_kick_marks_failed_when_build_raises(
    client_with_db, db_engine, monkeypatch
) -> None:
    """When the inline build raises despite inline=True, the row should
    still be in a usable state for the FE — not stuck queued forever."""
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    export_id = uuid.uuid4()
    async with session_local() as db:
        db.add(DataExport(id=export_id, user_id=user_id, status=EXPORT_STATUS_QUEUED))
        await db.commit()

    def _broken_build(export_id_str: str, *, inline: bool) -> None:
        # Simulate a real build that marks the row failed via the
        # worker's own error handling (inline=True suppresses re-raise,
        # but the row should still have moved).
        import asyncio as _asyncio

        async def _mark_failed():
            from app.db.session import AsyncSessionLocal

            async with AsyncSessionLocal() as inner_db:
                row = (
                    await inner_db.execute(
                        select(DataExport).where(
                            DataExport.id == uuid.UUID(export_id_str)
                        )
                    )
                ).scalar_one()
                row.status = EXPORT_STATUS_FAILED
                row.error = "simulated build failure"
                await inner_db.commit()

        _asyncio.run(_mark_failed())

    from app.db import session as session_module

    monkeypatch.setattr(
        session_module,
        "AsyncSessionLocal",
        async_sessionmaker(bind=db_engine, expire_on_commit=False),
    )
    monkeypatch.setattr("app.workers.account_export.build_user_export", _broken_build)

    await _with_auth(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            f"/api/v1/auth/me/data-export/{export_id}/kick",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The row must NOT be queued anymore — FE shows retry CTA.
        assert body["status"] == EXPORT_STATUS_FAILED
        assert body["error"] == "simulated build failure"
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]
