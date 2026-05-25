"""Render worker integration test (P0-11).

We stub Playwright so the test exercises the worker's full lifecycle —
status transitions, upload, ready timestamp — without needing a real
Chromium binary in CI. A separate manual smoke test exercises the
Playwright bridge against a real report page.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.report_render import (
    RENDER_STATUS_FAILED,
    RENDER_STATUS_QUEUED,
    RENDER_STATUS_READY,
    ReportRender,
)
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User


async def _seed_with_render_row(db_engine) -> uuid.UUID:
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    owner_id = uuid.uuid4()
    session_id = uuid.uuid4()
    submission_id = uuid.uuid4()
    render_id = uuid.uuid4()

    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=owner_id, email="o@a.local", display_name="O", handle="o"))
        db.add(
            Mission(
                id="m",
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
                user_id=owner_id,
                mission_id="m",
                status="graded",
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
                score_report={"total": 80},
                total_score=80,
                verification_hash="0" * 64,
                verification_signature="1" * 64,
            )
        )
        db.add(
            ReportRender(
                id=render_id,
                submission_id=submission_id,
                kind="pdf",
                status=RENDER_STATUS_QUEUED,
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()
    return render_id


@pytest.mark.asyncio
async def test_worker_marks_ready_on_success(db_engine, monkeypatch) -> None:
    render_id = await _seed_with_render_row(db_engine)
    from app.db import session as session_module
    from app.workers import report_render as worker_module

    captured: dict[str, object] = {}

    def _fake_render(target_url, kind, token, host_label="openagentdojo.app"):
        captured["target_url"] = target_url
        captured["kind"] = kind
        captured["token"] = token
        captured["host_label"] = host_label
        return b"%PDF-1.7 fake-payload"

    def _fake_put_object(key, body, *, content_type):
        captured["s3_key"] = key
        captured["content_type"] = content_type
        captured["bytes"] = len(body)
        return len(body)

    monkeypatch.setattr(worker_module, "_render_via_playwright", _fake_render)
    monkeypatch.setattr(worker_module, "put_object", _fake_put_object)

    # Call the async pipeline directly — render_report's asyncio.run
    # wrapper cannot nest inside pytest's loop.
    await worker_module._async_render(render_id, inline=True)

    async with session_module.AsyncSessionLocal() as db:
        from sqlalchemy import select

        row = (
            await db.execute(select(ReportRender).where(ReportRender.id == render_id))
        ).scalar_one()
        assert row.status == RENDER_STATUS_READY
        assert row.s3_key == captured["s3_key"]
        assert row.bytes == captured["bytes"]
        assert row.ready_at is not None
        assert "report-renders/" in row.s3_key
        assert captured["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_worker_marks_failed_on_render_exception(db_engine, monkeypatch) -> None:
    render_id = await _seed_with_render_row(db_engine)
    from app.db import session as session_module
    from app.workers import report_render as worker_module

    def _boom(target_url, kind, token, host_label="openagentdojo.app"):
        raise RuntimeError("simulated render failure")

    monkeypatch.setattr(worker_module, "_render_via_playwright", _boom)

    await worker_module._async_render(render_id, inline=True)

    async with session_module.AsyncSessionLocal() as db:
        from sqlalchemy import select

        row = (
            await db.execute(select(ReportRender).where(ReportRender.id == render_id))
        ).scalar_one()
        assert row.status == RENDER_STATUS_FAILED
        assert row.error and "render error" in row.error


@pytest.mark.asyncio
async def test_worker_degrades_gracefully_when_playwright_missing(db_engine, monkeypatch) -> None:
    render_id = await _seed_with_render_row(db_engine)
    from app.db import session as session_module
    from app.workers import report_render as worker_module

    def _missing(target_url, kind, token, host_label="openagentdojo.app"):
        raise worker_module._PlaywrightUnavailable("simulated missing binary")

    monkeypatch.setattr(worker_module, "_render_via_playwright", _missing)

    # inline=True path swallows the raise; the row should land at failed.
    await worker_module._async_render(render_id, inline=True)

    async with session_module.AsyncSessionLocal() as db:
        from sqlalchemy import select

        row = (
            await db.execute(select(ReportRender).where(ReportRender.id == render_id))
        ).scalar_one()
        assert row.status == RENDER_STATUS_FAILED
        assert row.error and "playwright unavailable" in row.error


def test_make_render_token_is_deterministic() -> None:
    from app.workers.report_render import make_render_token

    submission_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    render_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    a = make_render_token(submission_id, render_id, "secret")
    b = make_render_token(submission_id, render_id, "secret")
    assert a == b
    # Different secret → different token.
    assert make_render_token(submission_id, render_id, "secret2") != a
    # Hex sha256.
    assert len(a) == 64
    int(a, 16)
