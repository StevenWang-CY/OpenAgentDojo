"""P1-4 — workspace scratchpad endpoints.

Covers the GET/PUT ``/note`` round trip, 413 byte-cap rejection, 403
ownership enforcement, the 409 mutability gate, the ``note.edited``
coalescing window, the ``note.viewed_during_prompt`` POST, and the
"session reset preserves notes" invariant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_auth
from app.db.session import get_db
from app.main import create_app
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.session_note import SessionNote
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.sandbox.types import SandboxHandle

_CSRF = "test-csrf-token-notes"


def _csrf_kwargs() -> dict:
    return {
        "headers": {"X-CSRF-Token": _CSRF},
        "cookies": {"arena_csrf": _CSRF},
    }


def _make_app(db_session: AsyncSession, user: User):
    """Build a FastAPI app with DB + auth dependency overrides.

    Mirrors the pattern in ``tests/test_tutorial_endpoints.py`` — both
    routers use ``require_auth`` and ``get_db``, so the same overrides
    transport.
    """
    app = create_app()

    async def _override_db():
        yield db_session

    def _as_user() -> User:
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _as_user

    # The reset endpoint reaches for app.state.sandbox_pool; attach a
    # minimal fake so the test that drives /reset doesn't 503.
    app.state.sandbox_pool = _FakePool()
    return app


class _RunResult:
    def __init__(self, exit_code: int, stdout: str = "", stderr: str = "") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = 5


class _FakeDriver:
    name = "local"

    async def run(self, handle, *, cmd, timeout_s, cwd):
        # Default success; the reset endpoint only checks exit_code.
        return _RunResult(0)


class _FakePool:
    """Minimal sandbox-pool stub for tests that touch ``/reset``."""

    def __init__(self) -> None:
        self.driver = _FakeDriver()
        self._handles: list[SandboxHandle] = []

    def attach_handle(self, session_id: uuid.UUID, mission_id: str) -> None:
        self._handles.append(
            SandboxHandle(
                id=f"h-{uuid.uuid4().hex[:6]}",
                driver="local",
                workdir=Path("/tmp/arena-fake"),
                mission_id=mission_id,
                session_id=session_id,
            )
        )

    def handle_for(self, session_id):
        for h in self._handles:
            if h.session_id == session_id:
                return h
        return None

    def handles_snapshot(self):
        return list(self._handles)


@pytest_asyncio.fixture
async def notes_setup(db_session: AsyncSession) -> dict:
    """Seed a user + mission + active session for the scratchpad tests."""
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"u{unique}@example.com",
        handle=f"u{unique}",
        display_name="Owner",
    )
    other = User(
        email=f"o{unique}@example.com",
        handle=f"o{unique}",
        display_name="Other",
    )
    mission = Mission(
        id="notes-mission",
        title="Notes Mission",
        difficulty="beginner",
        category="testing",
        repo_pack="fullstack-auth-demo",
        initial_commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        estimated_minutes=10,
        failure_mode="test_failure_mode",
        skills_tested=["s"],
        manifest_sha256="0" * 64,
        version=1,
        published=True,
        expected_weak_dim="safety",
    )
    db_session.add_all([user, other, mission])
    await db_session.flush()

    session = SessionRow(
        user_id=user.id,
        mission_id="notes-mission",
        status="active",
        started_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add(session)
    await db_session.commit()
    return {"user": user, "other": other, "session": session}


# ---------------------------------------------------------------------------
# GET /note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_empty_returns_empty_body(
    notes_setup, db_session: AsyncSession
) -> None:
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session.id}/note")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["body"] == ""
    assert body["updated_at"] is not None

    # Crucially: the GET MUST NOT insert a row.
    row = (
        await db_session.execute(
            select(SessionNote).where(SessionNote.session_id == session.id)
        )
    ).scalar_one_or_none()
    assert row is None


# ---------------------------------------------------------------------------
# PUT /note — happy path + edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_persists_and_round_trips(
    notes_setup, db_session: AsyncSession
) -> None:
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    payload = "- check cookie expiry\n- inspect Date.now() usage\n"
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        put = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": payload},
            **_csrf_kwargs(),
        )
        assert put.status_code == 200, put.text
        assert put.json()["body"] == payload

        got = await ac.get(f"/api/v1/sessions/{session.id}/note")
        assert got.status_code == 200
        assert got.json()["body"] == payload


@pytest.mark.asyncio
async def test_put_rejects_oversized_body(
    notes_setup, db_session: AsyncSession
) -> None:
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    body = "a" * 32_769  # one byte over the cap
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": body},
            **_csrf_kwargs(),
        )
    # Pydantic's StringConstraints rejects with 422 *before* hitting the
    # router; either of {413, 422} is an acceptable "too large" envelope
    # since both surface to the FE as a hard rejection. We assert the
    # rejection happened AND nothing was persisted.
    assert resp.status_code in (413, 422), resp.text
    row = (
        await db_session.execute(
            select(SessionNote).where(SessionNote.session_id == session.id)
        )
    ).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_put_at_byte_cap_via_multibyte_chars_returns_413(
    notes_setup, db_session: AsyncSession
) -> None:
    """A body under the char cap but over the byte cap must 413.

    The Pydantic constraint counts characters; the router re-checks
    UTF-8 byte length so a 16385-char string of 2-byte chars still
    trips the byte cap with a clean ``scratchpad_too_large`` envelope.
    """
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    # 16385 copies of "é" (2 bytes each) = 32770 bytes; char-len 16385
    # is well under the 32768 char cap, so Pydantic accepts it.
    body = "é" * 16_385
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": body},
            **_csrf_kwargs(),
        )
    assert resp.status_code == 413, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "scratchpad_too_large"
    assert detail["limit_bytes"] == 32_768


@pytest.mark.asyncio
async def test_put_rejects_other_user(
    notes_setup, db_session: AsyncSession
) -> None:
    other = notes_setup["other"]
    session = notes_setup["session"]
    # Authenticate as the OTHER user — the session belongs to ``user``.
    app = _make_app(db_session, other)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "nope"},
            **_csrf_kwargs(),
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_put_on_inactive_session_409(
    notes_setup, db_session: AsyncSession
) -> None:
    user = notes_setup["user"]
    session = notes_setup["session"]
    # Mark the session as graded (the post-give-up terminal state). PUT
    # MUST 409 with ``session_not_active``.
    session.status = "graded"
    await db_session.commit()

    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "post-mortem doodles"},
            **_csrf_kwargs(),
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "session_not_active"


# ---------------------------------------------------------------------------
# note.edited supervision events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_emits_note_edited_event(
    notes_setup, db_session: AsyncSession
) -> None:
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    body = "hello\nworld\n"
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": body},
            **_csrf_kwargs(),
        )
    assert resp.status_code == 200, resp.text

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    payload = events[0].payload
    assert payload["bytes"] == len(body.encode("utf-8"))
    # "hello\nworld\n" has two trailing newlines → count("\n") + 1 == 3.
    assert payload["lines"] == 3
    assert payload["seconds_since_last_edit"] == 0


@pytest.mark.asyncio
async def test_put_coalesces_within_30s(
    notes_setup, db_session: AsyncSession
) -> None:
    """Three quick PUTs collapse into one ``note.edited`` row.

    The coalesced row's payload reflects the LAST write — the bytes/
    lines counts on the row match the final body, not the first.

    Also asserts the coalesced PUT queues a Redis publish so WS
    subscribers that already received the first event's id see the
    refreshed payload (otherwise the live timeline drifts from the
    DB-rendered one until the subscriber reconnects + backfills).
    """
    from app.sessions.events import _PENDING_KEY

    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        for body in ("first", "second draft", "third and final body"):
            resp = await ac.put(
                f"/api/v1/sessions/{session.id}/note",
                json={"body": body},
                **_csrf_kwargs(),
            )
            assert resp.status_code == 200, resp.text

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1, [e.payload for e in events]
    final_payload = events[0].payload
    assert final_payload["bytes"] == len(b"third and final body")
    assert final_payload["lines"] == 1

    # Inspect the pending-publishes queue on the SAME session the route
    # used. The dependency-override hands the test fixture's
    # ``db_session`` to every request, so all three PUTs share the
    # info dict — we should see at least 3 queued publishes (first
    # emit + two coalesce republishes) all targeting the same channel.
    pending = db_session.info.get(_PENDING_KEY, [])
    channels = {channel for channel, _ in pending}
    assert channels == {f"events:session:{session.id}"}, pending
    # First PUT emits via EventEmitter; the next two coalesce — so the
    # queue should carry at least the two coalesce republishes. Asserting
    # `>= 2` keeps the test resilient to any future change that batches
    # the emit-side publish with the coalesce one.
    assert len(pending) >= 2, pending
    # Each queued message MUST be valid JSON with the right shape so
    # WS subscribers can merge by id.
    import json as _json

    parsed = [_json.loads(msg) for _, msg in pending]
    assert all(p["event_type"] == "note.edited" for p in parsed)
    assert all(p["session_id"] == str(session.id) for p in parsed)
    # The id is shared across the three publishes — coalescing
    # preserves the row id so subscribers merge by it.
    ids = {p["id"] for p in parsed}
    assert len(ids) == 1, parsed


@pytest.mark.asyncio
async def test_put_serialises_concurrent_writes(
    notes_setup, db_session: AsyncSession
) -> None:
    """Two sequential PUTs on the same session collapse into ONE coalesced row.

    On SQLite the test harness is single-process and aiosqlite
    serialises writes, so a true concurrency stress test is not
    representative — the production race the FOR UPDATE lock guards
    against happens only on Postgres where two backends can
    simultaneously SELECT the same row. We exercise the path that
    DOES round-trip on SQLite: two back-to-back PUTs reaching the
    locked SELECT, ensuring the second writer sees the first's
    note.edited event and coalesces. Without the lock contract
    (and the in-place update in the coalesce branch) the second
    PUT would emit a fresh row even though the first PUT clearly
    landed first — so an extra supervision_events row would appear.
    """
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        first = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "race body A"},
            **_csrf_kwargs(),
        )
        second = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "race body B"},
            **_csrf_kwargs(),
        )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
            )
        )
        .scalars()
        .all()
    )
    # Exactly one coalesced event — the second writer saw the first
    # event inside the 30s window and updated in place. Row count is
    # 1, not 2.
    assert len(events) == 1, [e.payload for e in events]
    # The coalesced row's payload reflects the SECOND write.
    assert events[0].payload["bytes"] == len(b"race body B")


@pytest.mark.asyncio
async def test_put_does_not_coalesce_after_30s(
    notes_setup, db_session: AsyncSession
) -> None:
    """Back-date the prior event past the 30 s window and assert a new row fires."""
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r1 = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "first edit"},
            **_csrf_kwargs(),
        )
        assert r1.status_code == 200, r1.text

        # Back-date the existing note.edited event AND the SessionNote
        # row's updated_at so the next PUT computes a positive
        # seconds_since_last_edit AND falls outside the coalescing
        # window.
        far_past = datetime.now(UTC) - timedelta(minutes=5)
        ev = (
            await db_session.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
            )
        ).scalar_one()
        ev.occurred_at = far_past
        note_row = (
            await db_session.execute(
                select(SessionNote).where(SessionNote.session_id == session.id)
            )
        ).scalar_one()
        note_row.updated_at = far_past
        await db_session.commit()

        r2 = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "much later edit"},
            **_csrf_kwargs(),
        )
        assert r2.status_code == 200, r2.text

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent)
                .where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
                .order_by(SupervisionEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 2
    # The new event reflects the body of the SECOND write and reports
    # a positive seconds_since_last_edit.
    latest = events[-1]
    assert latest.payload["bytes"] == len(b"much later edit")
    assert latest.payload["seconds_since_last_edit"] >= 60


# ---------------------------------------------------------------------------
# note.viewed_during_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_note_viewed_event(
    notes_setup, db_session: AsyncSession
) -> None:
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            f"/api/v1/sessions/{session.id}/events/note-viewed",
            json={"bytes_at_view": 184},
            **_csrf_kwargs(),
        )
    assert resp.status_code == 204, resp.text

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.viewed_during_prompt",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].payload == {"bytes_at_view": 184}


# ---------------------------------------------------------------------------
# Reset preserves notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_note_survives_session_reset(
    notes_setup, db_session: AsyncSession
) -> None:
    """``POST /sessions/{id}/reset`` MUST leave session_notes untouched.

    The reset wipes the workspace + emits ``session.reset`` but the
    scratchpad is deliberately preserved across resets (see the
    P1_DESIGN §P1-4 contract: "the scratchpad survives so the user
    doesn't lose their notes when they start fresh on the code").
    """
    user = notes_setup["user"]
    session = notes_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)

    # Seed a scratchpad body, then verify it persists across /reset.
    pool = app.state.sandbox_pool
    pool.attach_handle(session.id, session.mission_id)

    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        put = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "persist me across the reset"},
            **_csrf_kwargs(),
        )
        assert put.status_code == 200, put.text

        reset = await ac.post(
            f"/api/v1/sessions/{session.id}/reset",
            **_csrf_kwargs(),
        )
        assert reset.status_code == 200, reset.text

        got = await ac.get(f"/api/v1/sessions/{session.id}/note")
        assert got.status_code == 200
        assert got.json()["body"] == "persist me across the reset"

    # And the underlying row is still present.
    row = (
        await db_session.execute(
            select(SessionNote).where(SessionNote.session_id == session.id)
        )
    ).scalar_one()
    assert row.body == "persist me across the reset"
