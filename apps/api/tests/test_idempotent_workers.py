"""Phase 4.A.T6 — workers skip when the row is already terminal.

``_async_render`` and ``_async_build_user_export`` MUST NOT re-execute
against a row whose status is ``ready`` or ``failed``. Re-running
would either overwrite the artefact OR flip a failed row back to
``running`` and lose the original error string.

We pre-populate a ready/failed row, call the worker, and assert no
upload / Playwright / build attempt was made — the row stays at its
terminal status.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.data_export import (
    EXPORT_STATUS_FAILED,
    EXPORT_STATUS_READY,
    DataExport,
)
from app.models.report_render import (
    RENDER_STATUS_FAILED,
    RENDER_STATUS_READY,
    ReportRender,
)
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _patch_session_local(db_engine, monkeypatch):
    """Point the workers at the in-memory test engine.

    Both workers do ``async with AsyncSessionLocal() as db:`` inside the
    function; we monkeypatch that symbol to the test-bound factory so
    the worker writes the same row the test asserts against.
    """
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", factory, raising=False)


async def _seed_user(session_factory) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            User(
                id=user_id,
                email=f"idemp-{user_id.hex[:8]}@a.local",
                handle=f"id-{user_id.hex[:6]}",
                session_epoch=1,
            )
        )
        await db.commit()
    return user_id


@pytest.mark.asyncio
async def test_async_render_skips_when_ready(session_factory, monkeypatch) -> None:
    user_id = await _seed_user(session_factory)
    submission_id = uuid.uuid4()
    render_id = uuid.uuid4()
    async with session_factory() as db:
        session = SessionRow(user_id=user_id, mission_id="m-x", status="graded")
        db.add(session)
        await db.flush()
        db.add(
            Submission(
                id=submission_id,
                session_id=session.id,
                final_diff="",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={"total": 80},
                total_score=80,
                created_at=datetime.now(UTC),
            )
        )
        db.add(
            ReportRender(
                id=render_id,
                submission_id=submission_id,
                kind="pdf",
                status=RENDER_STATUS_READY,
                s3_key="report-renders/x/pdf.pdf",
            )
        )
        await db.commit()

    # Trip-wires — Playwright invocation + S3 upload MUST NOT fire.
    playwright_calls: list[str] = []
    put_calls: list[str] = []

    def fake_playwright(*args, **kwargs):
        playwright_calls.append("called")
        raise AssertionError("playwright was invoked despite ready status")

    def fake_put(*args, **kwargs):
        put_calls.append("called")
        raise AssertionError("put_object was invoked despite ready status")

    monkeypatch.setattr("app.workers.report_render._render_via_playwright", fake_playwright)
    monkeypatch.setattr("app.workers.report_render.put_object", fake_put)

    from app.workers.report_render import _async_render

    await _async_render(render_id, inline=True)

    async with session_factory() as db:
        row = (
            await db.execute(select(ReportRender).where(ReportRender.id == render_id))
        ).scalar_one()
        # Status is still READY — no transition to RUNNING or back to FAILED.
        assert row.status == RENDER_STATUS_READY
    assert playwright_calls == []
    assert put_calls == []


@pytest.mark.asyncio
async def test_async_render_skips_when_failed(session_factory, monkeypatch) -> None:
    user_id = await _seed_user(session_factory)
    submission_id = uuid.uuid4()
    render_id = uuid.uuid4()
    async with session_factory() as db:
        session = SessionRow(user_id=user_id, mission_id="m-y", status="graded")
        db.add(session)
        await db.flush()
        db.add(
            Submission(
                id=submission_id,
                session_id=session.id,
                final_diff="",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={"total": 0},
                total_score=0,
                created_at=datetime.now(UTC),
            )
        )
        db.add(
            ReportRender(
                id=render_id,
                submission_id=submission_id,
                kind="pdf",
                status=RENDER_STATUS_FAILED,
                error="original failure",
            )
        )
        await db.commit()

    monkeypatch.setattr(
        "app.workers.report_render._render_via_playwright",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("playwright must not run on failed row")
        ),
    )
    from app.workers.report_render import _async_render

    await _async_render(render_id, inline=True)

    async with session_factory() as db:
        row = (
            await db.execute(select(ReportRender).where(ReportRender.id == render_id))
        ).scalar_one()
        assert row.status == RENDER_STATUS_FAILED
        # The original error message is preserved.
        assert row.error == "original failure"


@pytest.mark.asyncio
async def test_async_export_skips_when_ready(session_factory, monkeypatch) -> None:
    user_id = await _seed_user(session_factory)
    export_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            DataExport(
                id=export_id,
                user_id=user_id,
                status=EXPORT_STATUS_READY,
                s3_key=f"data-exports/{user_id}/{export_id}.zip",
            )
        )
        await db.commit()

    # Trip-wire — zip build must not be invoked on a ready row.
    build_calls: list[str] = []

    async def fake_build_zip(*args, **kwargs):
        build_calls.append("called")
        raise AssertionError("_build_zip was invoked despite ready status")

    monkeypatch.setattr("app.workers.account_export._build_zip", fake_build_zip)

    from app.workers.account_export import _async_build_user_export

    await _async_build_user_export(export_id, inline=True)

    async with session_factory() as db:
        row = (await db.execute(select(DataExport).where(DataExport.id == export_id))).scalar_one()
        assert row.status == EXPORT_STATUS_READY
    assert build_calls == []


@pytest.mark.asyncio
async def test_async_export_skips_when_failed(session_factory, monkeypatch) -> None:
    user_id = await _seed_user(session_factory)
    export_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            DataExport(
                id=export_id,
                user_id=user_id,
                status=EXPORT_STATUS_FAILED,
                error="original failure",
            )
        )
        await db.commit()

    monkeypatch.setattr(
        "app.workers.account_export._build_zip",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("build must not run on failed export")
        ),
    )

    from app.workers.account_export import _async_build_user_export

    await _async_build_user_export(export_id, inline=True)

    async with session_factory() as db:
        row = (await db.execute(select(DataExport).where(DataExport.id == export_id))).scalar_one()
        assert row.status == EXPORT_STATUS_FAILED
        assert row.error == "original failure"
