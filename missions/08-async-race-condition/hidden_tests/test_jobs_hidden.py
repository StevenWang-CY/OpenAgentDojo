"""Hidden tests for Mission 08 — Async Race Condition (Queue Processing)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import Job, init_schema, make_engine, make_session_factory
from app.jobs import process_job

_APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
async def session_factory() -> async_sessionmaker[object]:  # type: ignore[type-arg]
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_schema(engine)
    return make_session_factory(engine)


async def _seed_job(sf) -> int:  # type: ignore[no-untyped-def]
    async with sf() as s:
        job = Job(payload="x", status="pending")
        s.add(job)
        await s.commit()
        return int(job.id)


async def test_twenty_concurrent_workers_exactly_one_claims(session_factory) -> None:  # type: ignore[no-untyped-def]
    """20 workers fire process_job for the same id; exactly one wins."""
    job_id = await _seed_job(session_factory)
    results = await asyncio.gather(
        *[process_job(session_factory, job_id) for _ in range(20)]
    )
    claimed_count = sum(1 for r in results if r.claimed)
    assert claimed_count == 1, (
        f"expected exactly one worker to claim, got {claimed_count}"
    )


async def test_row_ends_in_done_with_processed_at_set(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _seed_job(session_factory)
    await asyncio.gather(
        *[process_job(session_factory, job_id) for _ in range(20)]
    )
    async with session_factory() as s:
        row = await s.get(Job, job_id)
    assert row is not None
    assert row.status == "done"
    assert row.processed_at is not None


def test_no_use_of_asyncio_lock_or_threading_lock_in_app_code() -> None:
    """Concurrency must be solved at the database level, not in Python."""
    jobs_src = (_APP_DIR / "jobs.py").read_text(encoding="utf-8")
    assert not re.search(r"asyncio\.Lock\s*\(", jobs_src), (
        "asyncio.Lock is not a cross-process concurrency primitive; use SQL"
    )
    assert not re.search(r"threading\.Lock\s*\(", jobs_src), (
        "threading.Lock is not a cross-process concurrency primitive; use SQL"
    )
