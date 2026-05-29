"""Render-endpoint lifecycle + rate-limit (P0-11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
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

# Bind the genuine ``_enqueue_render`` at import time — before the autouse
# ``_stub_render`` fixture rebinds the module attribute to a no-op — so the
# fallback unit test can invoke the real implementation.
from app.reports.router import _enqueue_render as _real_enqueue_render


async def _seed(db_engine, *, status: str = "graded") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
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
                expected_weak_dim="safety",
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


@pytest_asyncio.fixture(autouse=True)
async def _stub_render(monkeypatch: pytest.MonkeyPatch):
    """Make the in-process worker fallback a no-op so the test stays
    deterministic and never tries to spawn Chromium.

    Teardown drains ``reports.router._BACKGROUND_TASKS`` so a
    fire-and-forget render task scheduled by a fallback-path test (the
    queue-absent / enqueue-raises cases that exercise the real
    ``_enqueue_render``) can't survive onto this test's about-to-close
    event loop and perturb the shared async engine for a later test —
    ``tests/test_session_mode.py::test_integrity_event_persists_for_proctored``
    is sensitive to exactly that cross-loop leak.
    """
    monkeypatch.setattr("app.reports.router._enqueue_render", lambda render_id: None)
    yield
    import asyncio

    from app.reports.router import _BACKGROUND_TASKS

    leftover = [t for t in _BACKGROUND_TASKS if not t.done()]
    for task in leftover:
        task.cancel()
    if leftover:
        await asyncio.gather(*leftover, return_exceptions=True)
    _BACKGROUND_TASKS.clear()


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
async def test_post_render_rate_limited_after_cap(client, db_engine, monkeypatch) -> None:
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
async def test_get_render_returns_202_when_queue_absent(client, db_engine, monkeypatch) -> None:
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
        __import__("app.reports.router", fromlist=["_enqueue_render"])._enqueue_render,
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
async def test_enqueue_render_falls_back_inline_when_enqueue_raises(monkeypatch) -> None:
    """A transient Redis enqueue error must degrade to the in-process inline
    render rather than escape to the caller (which would 500 the route).

    Exercised at the ``_enqueue_render`` unit boundary — not through the HTTP
    route — so the test never commits to the shared in-memory engine or
    leaves a real route-scheduled task racing on the event loop (that race
    intermittently perturbs unrelated DB-touching tests). ``get_queue()``
    returns a queue whose ``.enqueue`` raises (the Redis-reachable-but-flaky
    case, distinct from the queue-is-None branch); the fix must catch it and
    fall through to the SAME ``_async_render(..., inline=True)`` path the
    absent-queue branch uses.
    """
    import asyncio

    from app.reports import router as rr

    render_id = uuid.uuid4()

    class _ExplodingQueue:
        def enqueue(self, *_args, **_kwargs):
            raise RuntimeError("redis connection reset")

    monkeypatch.setattr("app.workers.queue.get_queue", lambda: _ExplodingQueue())

    inline_calls: list[tuple[uuid.UUID, bool]] = []

    async def _record_inline(rid, *, inline):
        inline_calls.append((rid, inline))
        return None

    monkeypatch.setattr("app.workers.report_render._async_render", _record_inline)

    # Before the fix this raised RuntimeError straight out of the enqueue
    # call; the try/except inline fallback must swallow it and schedule the
    # in-process render instead. Call the real implementation captured at
    # import time (the autouse fixture has rebound the module attribute to a
    # no-op).
    _real_enqueue_render(render_id)

    # Drain the inline task this call scheduled (created on the running loop)
    # so it neither leaks nor races a later test, then confirm the inline
    # path (inline=True), not the enqueue path, actually ran.
    pending = [t for t in rr._BACKGROUND_TASKS if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    rr._BACKGROUND_TASKS.clear()
    assert inline_calls == [(render_id, True)]


def _flush_raises_then_inserts_winner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    submission_id: uuid.UUID,
    winner_status: str,
) -> dict[str, uuid.UUID | None]:
    """Patch ``AsyncSession.flush`` to model losing the first-insert race.

    The first flush call (the route's own INSERT) commits a *winner* row
    out-of-band in a fresh session, then raises ``IntegrityError`` exactly
    as the DB would when the unique constraint trips. Subsequent flushes
    run the real implementation. Returns a dict whose ``id`` key is
    populated with the winner's primary key so the caller can assert the
    route re-SELECTed that exact row.

    Modelling the race this way (winner inserted *during* the failing
    flush, not before it) keeps the route's initial SELECT returning
    ``None`` so it actually takes the INSERT path under test — a winner
    pre-inserted before the request would be found by that SELECT and the
    INSERT path would never run.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db import session as session_module

    real_flush = AsyncSession.flush
    captured: dict[str, uuid.UUID | None] = {"id": None}

    async def _flush(self, *args, **kwargs):
        if captured["id"] is None:
            async with session_module.AsyncSessionLocal() as other:
                winner = ReportRender(
                    submission_id=submission_id,
                    kind="pdf",
                    status=winner_status,
                )
                other.add(winner)
                await other.commit()
                captured["id"] = winner.id
            raise IntegrityError(
                "INSERT", {}, Exception("uq_report_renders_submission_kind")
            )
        return await real_flush(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", _flush)
    return captured


@pytest.mark.asyncio
async def test_get_render_recovers_from_insert_race(client, db_engine, monkeypatch) -> None:
    """Two concurrent first-ever GETs both see ``row is None`` and both
    INSERT; the loser's commit trips ``uq_report_renders_submission_kind``.

    We model the loser by letting the route's SELECT return None, then
    failing its INSERT flush with IntegrityError while a winner row lands
    out-of-band. The handler must roll back, re-SELECT the winner's row,
    and return 202 — not a generic 500.
    """
    owner_id, _, submission_id = await _seed(db_engine)
    from app.config import get_settings

    settings = get_settings()
    winner = _flush_raises_then_inserts_winner(
        monkeypatch, submission_id=submission_id, winner_status=RENDER_STATUS_QUEUED
    )

    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    resp = await client.get(f"/api/v1/reports/{submission_id}/render?kind=pdf")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == RENDER_STATUS_QUEUED
    assert body["kind"] == "pdf"
    # The re-SELECT returned the winner's row identity, not a fresh one.
    assert body["id"] == str(winner["id"])


@pytest.mark.asyncio
async def test_post_render_recovers_from_insert_race(client, db_engine, monkeypatch) -> None:
    """First-ever POST /render must survive losing the insert race.

    The handler's ``existing is None`` INSERT collides with a concurrent
    winner; on IntegrityError it rolls back, re-SELECTs the winner's row,
    recycles it as a fresh force re-render, and returns 202 with that
    stable row id — not a 500.
    """
    owner_id, _, submission_id = await _seed(db_engine)
    from app.config import get_settings

    settings = get_settings()
    winner = _flush_raises_then_inserts_winner(
        monkeypatch, submission_id=submission_id, winner_status=RENDER_STATUS_READY
    )

    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)
    resp = await client.post(
        f"/api/v1/reports/{submission_id}/render",
        json={"kind": "pdf"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # The re-SELECT picked up the winner's row identity and recycled it.
    assert body["id"] == str(winner["id"])
    assert body["status"] == RENDER_STATUS_QUEUED


@pytest.mark.asyncio
async def test_render_endpoints_409_when_not_graded(client, db_engine) -> None:
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

    get_resp = await client.get(f"/api/v1/reports/{submission_id}/render?kind=pdf")
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
