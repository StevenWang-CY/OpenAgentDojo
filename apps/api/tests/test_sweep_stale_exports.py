"""P1-6 — the stale-exports sweeper rescues wedged ``running`` rows.

Covers three states:

1. A ``running`` row older than the cutoff is flipped to ``failed`` with
   ``error='worker_lost'`` so the FE can offer a retry CTA.
2. A ``running`` row inside the cutoff window is left alone — the worker
   may still be legitimately building the zip.
3. A ``ready`` row is untouched no matter how old it is — terminal
   states are immune from the sweeper.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.data_export import (
    EXPORT_STATUS_FAILED,
    EXPORT_STATUS_READY,
    EXPORT_STATUS_RUNNING,
    DataExport,
)
from app.models.user import User
from scripts.sweep_stale_exports import sweep_stale_exports


@pytest_asyncio.fixture
async def session_local(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _bind_async_session(db_engine, monkeypatch):
    from app.db import session as session_module

    bound = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", bound)
    yield


async def _seed_user(session_local) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=f"sw-{user_id.hex[:8]}@test.local",
                handle=f"sw-{user_id.hex[:6]}",
                session_epoch=1,
            )
        )
        await db.commit()
    return user_id


async def _seed_export(
    session_local,
    *,
    user_id: uuid.UUID,
    status: str,
    age_minutes: int,
) -> uuid.UUID:
    export_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            DataExport(
                id=export_id,
                user_id=user_id,
                status=status,
                requested_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
            )
        )
        await db.commit()
    return export_id


async def _read_status(session_local, export_id: uuid.UUID) -> tuple[str, str | None]:
    async with session_local() as db:
        row = (
            await db.execute(
                select(DataExport).where(DataExport.id == export_id)
            )
        ).scalar_one()
        return row.status, row.error


@pytest.mark.asyncio
async def test_stale_running_row_is_flipped(session_local) -> None:
    user_id = await _seed_user(session_local)
    export_id = await _seed_export(
        session_local,
        user_id=user_id,
        status=EXPORT_STATUS_RUNNING,
        age_minutes=30,
    )
    flipped = await sweep_stale_exports(cutoff_minutes=15)
    assert flipped == 1

    status, error = await _read_status(session_local, export_id)
    assert status == EXPORT_STATUS_FAILED
    assert error == "worker_lost"


@pytest.mark.asyncio
async def test_fresh_running_row_is_left_alone(session_local) -> None:
    user_id = await _seed_user(session_local)
    export_id = await _seed_export(
        session_local,
        user_id=user_id,
        status=EXPORT_STATUS_RUNNING,
        age_minutes=1,
    )
    flipped = await sweep_stale_exports(cutoff_minutes=15)
    assert flipped == 0

    status, error = await _read_status(session_local, export_id)
    assert status == EXPORT_STATUS_RUNNING
    assert error is None


@pytest.mark.asyncio
async def test_ready_row_is_untouched_even_when_ancient(session_local) -> None:
    user_id = await _seed_user(session_local)
    export_id = await _seed_export(
        session_local,
        user_id=user_id,
        status=EXPORT_STATUS_READY,
        age_minutes=10_000,
    )
    flipped = await sweep_stale_exports(cutoff_minutes=15)
    assert flipped == 0

    status, error = await _read_status(session_local, export_id)
    assert status == EXPORT_STATUS_READY
    assert error is None


@pytest.mark.asyncio
async def test_second_pass_over_already_flipped_row_is_noop(session_local) -> None:
    """Sweeper is idempotent — flipped rows have status='failed' and
    are no longer eligible on the next run."""
    user_id = await _seed_user(session_local)
    await _seed_export(
        session_local,
        user_id=user_id,
        status=EXPORT_STATUS_RUNNING,
        age_minutes=60,
    )
    assert await sweep_stale_exports(cutoff_minutes=15) == 1
    assert await sweep_stale_exports(cutoff_minutes=15) == 0
