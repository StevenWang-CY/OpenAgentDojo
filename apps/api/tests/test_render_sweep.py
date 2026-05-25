"""Stuck-render sweep (P1-4).

A worker that crashed mid-render leaves the ``report_renders`` row at
``status='running'`` forever. The sweeper invoked periodically by the
FastAPI lifespan flips rows older than a configurable threshold to
``failed`` so the FE poll resolves and the user's force-rerender
budget isn't silently consumed.

Two contracts pinned here:

* a stuck ``running`` row older than the threshold is flipped to
  ``failed`` with the canonical error string;
* a fresh ``running`` row inside the threshold is NOT touched, and
  rows in any other status (``queued`` / ``ready`` / ``failed``) are
  ignored by the sweep entirely.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.report_render import (
    RENDER_STATUS_FAILED,
    RENDER_STATUS_QUEUED,
    RENDER_STATUS_READY,
    RENDER_STATUS_RUNNING,
    ReportRender,
)
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.workers.report_render import sweep_stuck_renders


async def _seed_submission(db_engine) -> uuid.UUID:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    submission_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with session_local() as db:
        db.add(User(id=user_id, email="s@arena.local", display_name="S", handle="s"))
        db.add(
            Mission(
                id="mid-sweep",
                title="t",
                difficulty="beginner",
                category="cat",
                repo_pack="p",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="f",
                skills_tested=["s"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="mid-sweep",
                status="graded",
                score=85,
                attempt_index=1,
            )
        )
        await db.flush()
        db.add(
            Submission(
                id=submission_id,
                session_id=session_id,
                final_diff="x",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={"total": 85},
                total_score=85,
            )
        )
        await db.commit()
    return submission_id


@pytest.mark.asyncio
async def test_sweep_flips_only_stuck_running_rows(db_engine) -> None:
    submission_id = await _seed_submission(db_engine)
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    now = datetime.now(UTC)
    stuck_id = uuid.uuid4()
    fresh_id = uuid.uuid4()
    queued_id = uuid.uuid4()
    ready_id = uuid.uuid4()

    async with session_local() as db:
        # 1. Stuck running row — created 20 minutes ago, well past the 5 min cap.
        stuck = ReportRender(
            id=stuck_id,
            submission_id=submission_id,
            kind="pdf",
            status=RENDER_STATUS_RUNNING,
        )
        db.add(stuck)
        await db.flush()
        stuck.created_at = now - timedelta(minutes=20)

        # 2. Fresh running row — inside the threshold; sweep MUST skip.
        fresh = ReportRender(
            id=fresh_id,
            submission_id=submission_id,
            kind="png",
            status=RENDER_STATUS_RUNNING,
        )
        db.add(fresh)
        await db.flush()
        fresh.created_at = now - timedelta(seconds=10)

        # 3. Queued row (different submission so the kind unique key holds).
        other_submission = uuid.uuid4()
        db.add(
            Submission(
                id=other_submission,
                session_id=stuck.submission_id,
                final_diff="y",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={"total": 1},
                total_score=1,
            )
        )
        await db.flush()
        queued = ReportRender(
            id=queued_id,
            submission_id=other_submission,
            kind="pdf",
            status=RENDER_STATUS_QUEUED,
        )
        db.add(queued)
        await db.flush()
        queued.created_at = now - timedelta(hours=1)  # old, but not RUNNING.

        # 4. Ready row — sweep must NEVER touch a terminal row.
        ready = ReportRender(
            id=ready_id,
            submission_id=other_submission,
            kind="png",
            status=RENDER_STATUS_READY,
            s3_key="x",
            bytes=1,
        )
        db.add(ready)
        await db.flush()
        ready.created_at = now - timedelta(days=1)

        await db.commit()

    async with session_local() as db:
        flipped = await sweep_stuck_renders(db, stale_after_s=300)

    assert flipped == 1
    async with session_local() as db:
        refreshed_stuck = await db.get(ReportRender, stuck_id)
        refreshed_fresh = await db.get(ReportRender, fresh_id)
        refreshed_queued = await db.get(ReportRender, queued_id)
        refreshed_ready = await db.get(ReportRender, ready_id)

    assert refreshed_stuck is not None
    assert refreshed_stuck.status == RENDER_STATUS_FAILED
    assert refreshed_stuck.error == "render_timed_out_after_shutdown"

    assert refreshed_fresh is not None
    assert refreshed_fresh.status == RENDER_STATUS_RUNNING

    assert refreshed_queued is not None
    assert refreshed_queued.status == RENDER_STATUS_QUEUED

    assert refreshed_ready is not None
    assert refreshed_ready.status == RENDER_STATUS_READY


@pytest.mark.asyncio
async def test_sweep_with_no_stuck_rows_is_noop(db_engine) -> None:
    """Idempotent: nothing to flip → returns 0, raises nothing."""
    submission_id = await _seed_submission(db_engine)
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async with session_local() as db:
        flipped = await sweep_stuck_renders(db, stale_after_s=300)
    assert flipped == 0

    # Add a single fresh running row and re-check.
    async with session_local() as db:
        row = ReportRender(
            submission_id=submission_id,
            kind="pdf",
            status=RENDER_STATUS_RUNNING,
        )
        db.add(row)
        await db.commit()

    async with session_local() as db:
        flipped = await sweep_stuck_renders(db, stale_after_s=300)
    assert flipped == 0
