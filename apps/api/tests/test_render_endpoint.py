"""Render-endpoint lifecycle + rate-limit (P0-11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.report_render import (
    RENDER_STATUS_QUEUED,
    RENDER_STATUS_READY,
    ReportRender,
)
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User


async def _seed(
    db_engine, *, status: str = "graded"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    owner_id = uuid.uuid4()
    session_id = uuid.uuid4()
    submission_id = uuid.uuid4()

    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=owner_id, email="o@arena.local", display_name="O", handle="o"))
        db.add(
            Mission(
                id="mid",
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
                mission_id="mid",
                status=status,
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
    return owner_id, session_id, submission_id


def _cookie(user_id: uuid.UUID) -> str:
    from app.auth.session_cookie import _ALGORITHM
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


_CSRF = "test-csrf-render-fixed"


def _csrf_kwargs() -> dict[str, object]:
    return {
        "headers": {"X-CSRF-Token": _CSRF},
        "cookies": {"arena_csrf": _CSRF},
    }


@pytest.fixture(autouse=True)
def _stub_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the in-process worker fallback a no-op so the test stays
    deterministic and never tries to spawn Chromium."""
    monkeypatch.setattr(
        "app.reports.router._enqueue_render", lambda render_id: None
    )


@pytest.mark.asyncio
async def test_get_render_enqueues_first_time(client, db_engine) -> None:
    owner_id, _, submission_id = await _seed(db_engine)
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))

    resp = await client.get(f"/api/v1/reports/{submission_id}/render?kind=pdf")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == RENDER_STATUS_QUEUED
    assert body["kind"] == "pdf"
    assert body["poll_after_seconds"] == 5


@pytest.mark.asyncio
async def test_get_render_returns_ready_redirect(client, db_engine, monkeypatch) -> None:
    owner_id, _, submission_id = await _seed(db_engine)
    from app.db import session as session_module

    # Flip the row to ready manually.
    async with session_module.AsyncSessionLocal() as db:
        row = ReportRender(
            submission_id=submission_id,
            kind="pdf",
            status=RENDER_STATUS_READY,
            s3_key=f"report-renders/{submission_id}/pdf.pdf",
            bytes=12345,
            ready_at=datetime.now(UTC),
        )
        db.add(row)
        await db.commit()

    monkeypatch.setattr(
        "app.reports.router.generate_download_url",
        lambda key, expires_in: f"https://example.invalid/{key}?sig=stub",
    )

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))

    resp = await client.get(
        f"/api/v1/reports/{submission_id}/render?kind=pdf", follow_redirects=False
    )
    assert resp.status_code == 302
    assert "example.invalid" in resp.headers["location"]


@pytest.mark.asyncio
async def test_get_render_rejects_invalid_kind(client, db_engine) -> None:
    owner_id, _, submission_id = await _seed(db_engine)
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    resp = await client.get(f"/api/v1/reports/{submission_id}/render?kind=svg")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_render_idempotent_during_inflight(client, db_engine) -> None:
    owner_id, _, submission_id = await _seed(db_engine)
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    # First force-render — creates a queued row.
    r1 = await client.post(
        f"/api/v1/reports/{submission_id}/render",
        json={"kind": "pdf"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert r1.status_code == 202, r1.text
    row_id = r1.json()["id"]

    # Second force-render while still queued — should return the SAME row.
    r2 = await client.post(
        f"/api/v1/reports/{submission_id}/render",
        json={"kind": "pdf"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert r2.status_code == 202, r2.text
    assert r2.json()["id"] == row_id


@pytest.mark.asyncio
async def test_post_render_rate_limited_after_cap(
    client, db_engine, monkeypatch
) -> None:
    owner_id, _, submission_id = await _seed(db_engine)
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    # Saturate via a stubbed counter so we don't need to insert
    # CHECK-violating rows. The route calls _force_renders_today; we
    # stub that to return the cap so the next POST trips the limit.
    async def _at_cap(_db, _submission_id):
        return settings.report_render_force_daily_cap

    monkeypatch.setattr("app.reports.router._force_renders_today", _at_cap)

    resp = await client.post(
        f"/api/v1/reports/{submission_id}/render",
        json={"kind": "pdf"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"]["code"] == "force_render_rate_limited"


@pytest.mark.asyncio
async def test_get_render_returns_202_when_queue_absent(
    client, db_engine, monkeypatch
) -> None:
    """When ``get_queue()`` returns None (no Redis), the in-process
    fallback used to call ``asyncio.run`` inside FastAPI's running loop
    and 500. The fix schedules the underlying coroutine via
    ``create_task``; the route must still respond 202 and create the
    queued row.
    """
    owner_id, _, submission_id = await _seed(db_engine)
    from app.config import get_settings

    settings = get_settings()

    # Undo the autouse stub so we exercise the real _enqueue_render
    # against a stubbed queue + a no-op _async_render.
    monkeypatch.setattr(
        "app.reports.router._enqueue_render",
        __import__(
            "app.reports.router", fromlist=["_enqueue_render"]
        )._enqueue_render,
    )
    monkeypatch.setattr("app.workers.queue.get_queue", lambda: None)

    async def _noop(render_id, *, inline):
        return None

    monkeypatch.setattr("app.workers.report_render._async_render", _noop)

    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    resp = await client.get(f"/api/v1/reports/{submission_id}/render?kind=pdf")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == RENDER_STATUS_QUEUED


@pytest.mark.asyncio
async def test_render_endpoints_409_when_not_graded(
    client, db_engine
) -> None:
    """GET + POST /render must mirror /verify's graded-state gate.

    A submission whose session is still 'submitting' (or 'error', etc.)
    has no envelope to anchor the PDF against — the render must 409
    instead of queuing a job whose output can't be verified.
    """
    owner_id, _, submission_id = await _seed(db_engine, status="submitting")
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    get_resp = await client.get(
        f"/api/v1/reports/{submission_id}/render?kind=pdf"
    )
    assert get_resp.status_code == 409, get_resp.text
    assert get_resp.json()["detail"]["code"] == "not_graded"

    post_resp = await client.post(
        f"/api/v1/reports/{submission_id}/render",
        json={"kind": "pdf"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert post_resp.status_code == 409, post_resp.text
    assert post_resp.json()["detail"]["code"] == "not_graded"


@pytest.mark.asyncio
async def test_render_owner_required_for_post(client, db_engine) -> None:
    # No cookie attached → 401 (auth required). CSRF is still attached so
    # the rejection is purely the auth gate.
    _owner, _, submission_id = await _seed(db_engine)
    client.cookies.set("arena_csrf", _CSRF)
    resp = await client.post(
        f"/api/v1/reports/{submission_id}/render",
        json={"kind": "pdf"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 401, resp.text
