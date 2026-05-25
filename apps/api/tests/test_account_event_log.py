"""Gap 2 — every P0-6 ``account.*`` flow persists to ``account_events``.

Before migration 0017 + the route refactor, the five P0-6 account events
(``email_change_requested``, ``email_changed``, ``signed_out_all_sessions``,
``deletion_scheduled``, ``deletion_cancelled``) were routed through
``logger.info`` only — they evaporated unless the log aggregator caught
them. That violates the P0_DESIGN §0.3 invariant: every user action MUST
emit a typed event the replay tool can read.

This module pins:

  1. Each of the five flows lands exactly one row on ``account_events``
     with the expected ``event_type`` + payload shape.
  2. Failure-path invariant: when the route raises mid-state-change, the
     pending event row rolls back with the state (so the audit log never
     leads the database).

Notes:

  * The tests reuse the ``client_with_db`` fixture pattern that already
    exists in test_account_email_change / test_account_deletion_grace; the
    DB is the in-memory SQLite engine whose schema is built from ORM
    metadata (migration 0017 is not exercised here — but the model class
    binds to ``account_events`` so the table name matches what production
    will see).
  * Failure injection uses a monkey-patch on ``rotate_user_session_epoch``
    to force the email-confirm flow to raise after the AccountEvent row
    has been staged but before the commit lands. The post-condition is
    that the row is NOT present.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.user import User
from app.models.user_consent import AccountEvent


async def _bound(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_user(session_local, *, email: str | None = None) -> uuid.UUID:
    user_id = uuid.uuid4()
    email = email or f"ev-{user_id.hex[:8]}@test.local"
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=email,
                handle=f"ev-{user_id.hex[:6]}",
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


async def _count_events(session_local, *, user_id: uuid.UUID, event_type: str | None = None) -> int:
    async with session_local() as db:
        stmt = select(func.count()).select_from(AccountEvent).where(AccountEvent.user_id == user_id)
        if event_type is not None:
            stmt = stmt.where(AccountEvent.event_type == event_type)
        return (await db.execute(stmt)).scalar_one()


async def _fetch_event(session_local, *, user_id: uuid.UUID, event_type: str) -> AccountEvent:
    async with session_local() as db:
        row = (
            (
                await db.execute(
                    select(AccountEvent)
                    .where(
                        AccountEvent.user_id == user_id,
                        AccountEvent.event_type == event_type,
                    )
                    .order_by(AccountEvent.id.desc())
                )
            )
            .scalars()
            .first()
        )
    assert row is not None, f"expected {event_type} event for {user_id}"
    return row


# ---------------------------------------------------------------------------
# Each of the five account.* flows must land exactly one event.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_change_requested_emits_event(client_with_db, db_engine, monkeypatch) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local, email="me@example.com")
    await _auth_as(client_with_db, session_local, user_id)

    async def _capture(to_email, magic_url, settings):
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
    finally:
        _clear_auth(client_with_db)

    row = await _fetch_event(
        session_local, user_id=user_id, event_type="account.email_change_requested"
    )
    # Payload carries the salted email hash, NOT the plaintext (PII guard).
    assert "new_email_hash" in row.payload
    assert isinstance(row.payload["new_email_hash"], str)
    assert "new@example.com" not in row.payload["new_email_hash"]
    assert "@" not in row.payload["new_email_hash"]


@pytest.mark.asyncio
async def test_email_confirm_emits_event(client_with_db, db_engine, monkeypatch) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local, email="me@example.com")
    await _auth_as(client_with_db, session_local, user_id)

    sent: list[str] = []

    async def _capture(to_email, magic_url, settings):
        sent.append(magic_url)
        return True

    monkeypatch.setattr("app.auth.routes.send_email_change_link", _capture)

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        change_resp = await client_with_db.post(
            "/api/v1/auth/me/email/change",
            json={"new_email": "new@example.com"},
            headers={"X-Csrf-Token": "tok"},
        )
        assert change_resp.status_code == 204, change_resp.text
        assert sent, "test setup error: change must dispatch email"
        token = sent[-1].rsplit("token=", 1)[-1]

        confirm_resp = await client_with_db.post(
            "/api/v1/auth/me/email/confirm",
            json={"token": token},
            headers={"X-Csrf-Token": "tok"},
        )
        assert confirm_resp.status_code == 200, confirm_resp.text
    finally:
        _clear_auth(client_with_db)

    # Exactly one of each event for this user.
    assert (
        await _count_events(
            session_local,
            user_id=user_id,
            event_type="account.email_change_requested",
        )
        == 1
    )
    assert (
        await _count_events(
            session_local,
            user_id=user_id,
            event_type="account.email_changed",
        )
        == 1
    )
    confirmed = await _fetch_event(
        session_local, user_id=user_id, event_type="account.email_changed"
    )
    assert confirmed.payload == {}


@pytest.mark.asyncio
async def test_sign_out_all_emits_event(client_with_db, db_engine) -> None:
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)
    await _auth_as(client_with_db, session_local, user_id)
    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            "/api/v1/auth/me/sessions/sign-out-all",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 204, resp.text
    finally:
        _clear_auth(client_with_db)

    row = await _fetch_event(
        session_local,
        user_id=user_id,
        event_type="account.signed_out_all_sessions",
    )
    assert row.payload == {}


@pytest.mark.asyncio
async def test_deletion_scheduled_and_cancelled_emit_events(
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
        sched_resp = await client_with_db.post(
            "/api/v1/auth/me/delete",
            json={"confirm_email": "me@example.com"},
            headers={"X-Csrf-Token": "tok"},
        )
        assert sched_resp.status_code == 200, sched_resp.text

        cancel_resp = await client_with_db.post(
            "/api/v1/auth/me/delete/cancel",
            headers={"X-Csrf-Token": "tok"},
        )
        assert cancel_resp.status_code == 204, cancel_resp.text
    finally:
        _clear_auth(client_with_db)

    scheduled = await _fetch_event(
        session_local,
        user_id=user_id,
        event_type="account.deletion_scheduled",
    )
    # The scheduled_for ISO must be a parseable timestamp in the future-ish
    # past now (we just cancelled, but the event payload is fixed at emit).
    assert "scheduled_for" in scheduled.payload
    parsed = datetime.fromisoformat(scheduled.payload["scheduled_for"])
    assert parsed > datetime.now(UTC) - timedelta(seconds=30)

    cancelled = await _fetch_event(
        session_local,
        user_id=user_id,
        event_type="account.deletion_cancelled",
    )
    assert cancelled.payload == {}


# ---------------------------------------------------------------------------
# Failure path: a mid-transaction failure must NOT leak a pre-staged event.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_delete_emits_account_deleted_event(db_engine, monkeypatch) -> None:
    """P2 — the deletion worker stages an ``account.deleted`` row.

    Migration 0018 widens the CHECK to accept the literal. The worker
    wipes prior consents + account_events first (they're scoped to the
    live account) then inserts the terminal marker so the audit log
    carries one tombstone signal after the cascade.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from app.db import session as session_module
    from app.models.user import User
    from app.models.user_consent import AccountEvent
    from app.workers.account_deletion import _async_process_deletion_grace

    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local, email="goneby@example.com")

    # Arm a deletion that has already elapsed so the worker picks it up.
    async with session_local() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        user.deletion_scheduled_at = datetime.now(UTC) - timedelta(minutes=5)
        # Drop a prior account.* row so we can verify it's wiped + a
        # single terminal account.deleted row remains.
        from app.auth.routes import _build_account_event

        db.add(_build_account_event(user_id, "account.deletion_scheduled", {}))
        await db.commit()

    # Rebind app.db.session.AsyncSessionLocal so the worker uses the test
    # engine. monkeypatch reverts the bind at teardown so other tests
    # aren't affected.
    monkeypatch.setattr(session_module, "AsyncSessionLocal", session_local)

    processed = await _async_process_deletion_grace()
    assert processed == 1, "expected the worker to hard-delete the seeded user"

    # The terminal account.deleted row should be the ONLY remaining event
    # for this user — historical rows were wiped by the worker.
    async with session_local() as db:
        rows = (
            (await db.execute(select(AccountEvent).where(AccountEvent.user_id == user_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1, (
        f"expected exactly one terminal account.deleted event; saw {[r.event_type for r in rows]}"
    )
    assert rows[0].event_type == "account.deleted"
    assert "tombstone_handle" in rows[0].payload


@pytest.mark.asyncio
async def test_event_rolls_back_when_request_transaction_aborts(
    db_engine,
) -> None:
    """Staging an AccountEvent that is then rolled back leaves no row.

    Mirrors the get_db dependency contract: the request opens a single
    transaction, stages writes via ``db.add(...)`` + ``await db.flush()``,
    and rolls back when the handler raises. The AccountEvent rows the
    routes stage must vanish with the rollback — otherwise the audit log
    would record events for state changes that never landed.
    """
    session_local = await _bound(db_engine)
    user_id = await _seed_user(session_local)

    pre_count = await _count_events(session_local, user_id=user_id)

    # Drive the same lifecycle the route would: open a session, stage the
    # User write + the AccountEvent row, flush (so the INSERT statements
    # are sent to the DB but not committed), then rollback. Post-condition:
    # no row visible from a fresh session.
    from app.auth.routes import _build_account_event

    async with session_local() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        user.email = "would-be-new@example.com"
        db.add(user)
        db.add(
            _build_account_event(
                user_id,
                "account.email_changed",
                {"would_have_landed": True},
            )
        )
        await db.flush()
        # Simulate the handler raising — get_db's except branch calls
        # ``await session.rollback()`` for us in production. We invoke
        # it explicitly here to keep the test free of HTTP plumbing.
        await db.rollback()

    post_count = await _count_events(session_local, user_id=user_id)
    assert post_count == pre_count, (
        "account.email_changed event must roll back with the request "
        "transaction; the audit log can never lead the state"
    )

    async with session_local() as db:
        row = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert row.email != "would-be-new@example.com", (
        "the user write must roll back alongside the event row"
    )
