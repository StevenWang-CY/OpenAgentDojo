"""``sweep_stuck_exports`` recovers data-export rows abandoned by RQ.

Twin failure modes covered:

1. **Orphaned ``queued`` row** — the route enqueued into RQ but no
   worker consumed the job (queue had no live consumer). Without the
   sweep the FE polls a row that never moves; the user sees the
   "Queued — your export will start shortly" banner indefinitely.

   The sweep rescues by running the build inline. Either the build
   succeeds (row → ``ready``) or fails cleanly (row → ``failed``);
   either way the row leaves ``queued`` and the user is unblocked.

2. **Wedged ``running`` row** — worker crashed mid-build (OOM, SIGTERM
   during shutdown, S3 upload hang). The row pins at ``running``
   forever; the in-flight unique index blocks the user from queueing
   a fresh export.

   The sweep flips the row to ``failed`` after a configurable
   timeout so the user can retry.

Both branches must be idempotent (a second sweep against the same
state is a no-op) and race-safe (the worker's own terminal-state
early-return guards against double work if a slow worker finishes
during the sweep).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
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
from app.workers.account_export import sweep_stuck_exports


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
        db.add(User(id=user_id, email=f"u-{user_id}@arena.local", display_name="U", handle=f"u{user_id.hex[:6]}"))
        await db.commit()
    return user_id


@pytest.mark.asyncio
async def test_sweep_rescues_orphaned_queued_export(session_local):
    """A ``queued`` row older than the threshold and orphaned by RQ
    gets picked up by the sweep and either lands ``ready`` or
    ``failed`` — never stays ``queued``."""
    user_id = await _seed_user(session_local)
    export_id = uuid.uuid4()

    async with session_local() as db:
        row = DataExport(id=export_id, user_id=user_id, status=EXPORT_STATUS_QUEUED)
        db.add(row)
        await db.flush()
        # Backdate so the queued_horizon catches it.
        row.requested_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.commit()

    # Stub the inline build path so we don't actually walk every user
    # table — we just want to verify the sweep CALLS the build and the
    # row leaves the queued state.
    async def _mark_ready(export_id_str: str, *, inline: bool):
        assert inline is True
        assert export_id_str == str(export_id)
        async with session_local() as db:
            row = await db.get(DataExport, export_id)
            row.status = EXPORT_STATUS_READY
            row.s3_key = "stub/key"
            row.bytes_total = 42
            row.ready_at = datetime.now(UTC)
            row.expires_at = datetime.now(UTC) + timedelta(days=7)
            await db.commit()

    def _stub_build(export_id_str: str, *, inline: bool):
        # asyncio.to_thread wraps a sync callable; replicate that.
        import asyncio

        asyncio.run(_mark_ready(export_id_str, inline=inline))

    with patch("app.workers.account_export.build_user_export", side_effect=_stub_build):
        async with session_local() as db:
            rescued, failed = await sweep_stuck_exports(
                db,
                queued_stale_after_s=60,
                running_stale_after_s=600,
            )

    assert rescued == 1
    assert failed == 0

    async with session_local() as db:
        refreshed = await db.get(DataExport, export_id)
        assert refreshed.status == EXPORT_STATUS_READY
        assert refreshed.s3_key == "stub/key"


@pytest.mark.asyncio
async def test_sweep_skips_fresh_queued_export(session_local):
    """A queued row inside the threshold is left alone — a healthy
    worker is likely about to pick it up."""
    user_id = await _seed_user(session_local)
    export_id = uuid.uuid4()

    async with session_local() as db:
        row = DataExport(id=export_id, user_id=user_id, status=EXPORT_STATUS_QUEUED)
        db.add(row)
        await db.flush()
        row.requested_at = datetime.now(UTC) - timedelta(seconds=5)
        await db.commit()

    sentinel_called = {"n": 0}

    def _sentinel_build(*args, **kwargs):
        sentinel_called["n"] += 1

    with patch("app.workers.account_export.build_user_export", side_effect=_sentinel_build):
        async with session_local() as db:
            rescued, failed = await sweep_stuck_exports(
                db,
                queued_stale_after_s=60,
                running_stale_after_s=600,
            )

    assert rescued == 0
    assert failed == 0
    assert sentinel_called["n"] == 0

    async with session_local() as db:
        refreshed = await db.get(DataExport, export_id)
        assert refreshed.status == EXPORT_STATUS_QUEUED


@pytest.mark.asyncio
async def test_sweep_flips_wedged_running_export(session_local):
    """A running row older than the running threshold gets flipped to
    failed with the canonical error string, so the partial unique
    index releases and the user can retry."""
    user_id = await _seed_user(session_local)
    export_id = uuid.uuid4()

    async with session_local() as db:
        row = DataExport(id=export_id, user_id=user_id, status=EXPORT_STATUS_RUNNING)
        db.add(row)
        await db.flush()
        row.requested_at = datetime.now(UTC) - timedelta(minutes=30)
        await db.commit()

    async with session_local() as db:
        rescued, failed = await sweep_stuck_exports(
            db,
            queued_stale_after_s=60,
            running_stale_after_s=600,
        )

    assert rescued == 0
    assert failed == 1

    async with session_local() as db:
        refreshed = await db.get(DataExport, export_id)
        assert refreshed.status == EXPORT_STATUS_FAILED
        assert refreshed.error == "export_worker_wedged_after_timeout"


@pytest.mark.asyncio
async def test_sweep_ignores_terminal_states(session_local):
    """Ready and failed rows must NEVER be touched by the sweep, no
    matter how old."""
    user_a = await _seed_user(session_local)
    user_b = await _seed_user(session_local)
    ready_id = uuid.uuid4()
    failed_id = uuid.uuid4()

    async with session_local() as db:
        ready = DataExport(
            id=ready_id,
            user_id=user_a,
            status=EXPORT_STATUS_READY,
            s3_key="x",
            bytes_total=1,
            ready_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        failed = DataExport(
            id=failed_id,
            user_id=user_b,
            status=EXPORT_STATUS_FAILED,
            error="prior_failure",
        )
        db.add_all([ready, failed])
        await db.flush()
        ready.requested_at = datetime.now(UTC) - timedelta(days=2)
        failed.requested_at = datetime.now(UTC) - timedelta(days=2)
        await db.commit()

    async with session_local() as db:
        rescued, flipped = await sweep_stuck_exports(
            db,
            queued_stale_after_s=60,
            running_stale_after_s=600,
        )

    assert rescued == 0
    assert flipped == 0

    async with session_local() as db:
        assert (await db.get(DataExport, ready_id)).status == EXPORT_STATUS_READY
        refreshed_failed = await db.get(DataExport, failed_id)
        assert refreshed_failed.status == EXPORT_STATUS_FAILED
        assert refreshed_failed.error == "prior_failure"


@pytest.mark.asyncio
async def test_sweep_is_noop_when_no_stuck_rows(session_local):
    """Idempotent on an empty table."""
    async with session_local() as db:
        rescued, failed = await sweep_stuck_exports(
            db,
            queued_stale_after_s=60,
            running_stale_after_s=600,
        )
    assert rescued == 0
    assert failed == 0
