"""Service-layer guarantees for ``app.sessions.service`` (P1-3).

``set_sandbox`` historically returned silently when the row was missing —
masking the provision-after-reap race where the worker tried to attach a
sandbox handle to a session id the orphan sweeper had already deleted. The
fix raises :class:`SessionNotFoundError` so callers must explicitly handle
or escalate the condition.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User
from app.sessions.service import (
    SessionNotFoundError,
    set_sandbox,
    set_status,
)


async def _bound_session(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_set_sandbox_raises_when_session_missing(db_engine) -> None:
    """A non-existent session id must surface as ``SessionNotFoundError``."""
    SessionLocal = await _bound_session(db_engine)
    missing_id = uuid.uuid4()

    async with SessionLocal() as db:
        with pytest.raises(SessionNotFoundError) as ei:
            await set_sandbox(db, missing_id, "ignored-handle-id")
        assert ei.value.session_id == missing_id
        # Stringified exception carries the id so log scrapers can dedupe.
        assert str(missing_id) in str(ei.value)


@pytest.mark.asyncio
async def test_set_sandbox_persists_when_session_exists(db_engine) -> None:
    """Happy path: the column flips and commits when the row is present."""
    SessionLocal = await _bound_session(db_engine)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="sb-test@arena.local",
                display_name="SB",
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Auth",
                difficulty="intermediate",
                category="auth",
                repo_pack="x",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="provisioning",
            )
        )
        await db.commit()

    async with SessionLocal() as db:
        await set_sandbox(db, session_id, "sb-handle-42")
        # Read back from a fresh session — confirms commit happened.
    async with SessionLocal() as db:
        from sqlalchemy import select

        row = (await db.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
        assert row.sandbox_id == "sb-handle-42"


@pytest.mark.asyncio
async def test_set_status_is_still_silent_for_missing_session(db_engine) -> None:
    """``set_status`` retains its historic no-op behaviour for unknown ids.

    Only ``set_sandbox`` raises — ``set_status`` is invoked from cleanup paths
    (orphan sweeper, error handlers) where the row may legitimately have been
    deleted. The contract is documented in the service module docstring.
    """
    SessionLocal = await _bound_session(db_engine)
    async with SessionLocal() as db:
        # No exception raised, no DB change.
        await set_status(db, uuid.uuid4(), "error")
