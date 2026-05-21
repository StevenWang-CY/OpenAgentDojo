"""Visible unit tests for the queue processor.

Cover the happy path (one worker, one job) and the trivial double-claim
case via a single-session sequence. The 20-way ``asyncio.gather`` race
that catches Mission 08's broken patch lives in the hidden suite.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import Job, init_schema, make_engine, make_session_factory
from app.jobs import process_job


@pytest.fixture
async def session_factory() -> async_sessionmaker[object]:  # type: ignore[type-arg]
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_schema(engine)
    return make_session_factory(engine)


async def _seed_job(session_factory, payload: str = "hello") -> int:  # type: ignore[no-untyped-def]
    async with session_factory() as s:
        job = Job(payload=payload, status="pending")
        s.add(job)
        await s.commit()
        return int(job.id)


async def test_single_worker_claims_and_finishes(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _seed_job(session_factory)
    result = await process_job(session_factory, job_id)
    assert result.claimed is True
    assert result.done is True

    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.status == "done"
        assert row.processed_at is not None


async def test_second_sequential_call_returns_not_claimed(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _seed_job(session_factory)
    first = await process_job(session_factory, job_id)
    second = await process_job(session_factory, job_id)
    assert first.claimed is True
    assert second.claimed is False
