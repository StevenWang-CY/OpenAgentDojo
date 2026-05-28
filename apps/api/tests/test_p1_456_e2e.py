"""Wave 4 end-to-end audit for P1-4 / P1-5 / P1-6.

Exercises the scratchpad, coaching, replay, and three-way diff
surfaces through the live FastAPI app under
``httpx.AsyncClient(transport=ASGITransport(...))`` — the in-process
counterpart of a real HTTP client. The intent is to catch contract
drift in any of the four feature surfaces with one cohesive run that
runs against the same in-memory SQLite test fixture the rest of the
suite uses.

This file intentionally repeats some of the per-surface tests in
``tests/sessions/test_notes.py``, ``tests/reports/test_coaching.py``,
``tests/reports/test_replay.py`` rather than importing helpers from
them — the surface-spanning view is what audits the system; the
per-surface files audit each route in isolation.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.deps import require_auth
from app.db.base import Base
from app.db.session import get_db
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.session_note import SessionNote
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.reports import coaching as coaching_module
from app.reports.replay import PROMPT_BEARING_EVENT_TYPES
from app.sandbox.types import SandboxHandle

# ---------------------------------------------------------------------------
# Pinned identifiers — kept distinct from the per-surface test files so
# this audit can run alongside them in the same pytest session.
# ---------------------------------------------------------------------------

_OWNER_ID = uuid.UUID("d1111111-1111-4111-8111-111111111111")
_STRANGER_ID = uuid.UUID("d2222222-2222-4222-8222-222222222222")
_SESSION_ID = uuid.UUID("d3333333-3333-4333-8333-333333333333")
_SUBMISSION_ID = uuid.UUID("d4444444-4444-4444-8444-444444444444")
_MISSION_ID = "p1-456-audit-mission"
_NOTE_BODY_INITIAL = "hypothesis: check the cookie expiration"
_NOTE_BODY_UPDATED = "hypothesis: check expiration AND TTL"

_CSRF_TOKEN = "wave4-audit-csrf-token"


def _csrf() -> dict[str, Any]:
    return {
        "headers": {"X-CSRF-Token": _CSRF_TOKEN},
        "cookies": {"arena_csrf": _CSRF_TOKEN},
    }


# ---------------------------------------------------------------------------
# Sandbox-pool fake — required for ``POST /sessions/{id}/reset``.
# ---------------------------------------------------------------------------


class _RunResult:
    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.stdout = ""
        self.stderr = ""
        self.duration_ms = 5


class _FakeDriver:
    name = "local"

    async def run(self, handle, *, cmd, timeout_s, cwd):
        return _RunResult(0)


class _FakePool:
    def __init__(self) -> None:
        self.driver = _FakeDriver()
        self._handles: list[SandboxHandle] = []

    def attach(self, session_id: uuid.UUID, mission_id: str) -> None:
        self._handles.append(
            SandboxHandle(
                id=f"h-{uuid.uuid4().hex[:8]}",
                driver="local",
                workdir=Path("/tmp/arena-fake"),
                mission_id=mission_id,
                session_id=session_id,
            )
        )

    def handle_for(self, session_id: uuid.UUID) -> SandboxHandle | None:
        for h in self._handles:
            if h.session_id == session_id:
                return h
        return None

    def handles_snapshot(self) -> list[SandboxHandle]:
        return list(self._handles)


# ---------------------------------------------------------------------------
# Coaching LLM stub — mirrors the pattern in tests/reports/test_coaching.py.
# ---------------------------------------------------------------------------


def _content_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


class _StubCoachingClient:
    """Deterministic LLM stub that emits the anchored coaching prose
    the audit expects. The output carries both ``[event:N]`` and
    ``[note:"..."]`` markers so the route's anchor-parsing path is
    exercised."""

    # ``[event:1]`` references the lone ``prompt.submitted`` event the
    # test seeds — coaching now validates anchor ids against the
    # loaded events list and drops dangling references, so the
    # marker must match a real id for ``anchored_event_id`` to
    # survive parsing.
    DEFAULT_OUTPUT = (
        "Early on you sent the agent a prompt [event:1] without first "
        'checking your scratchpad — your own notes flagged [note:"check '
        'the cookie expiration"], which was the right hypothesis the '
        "model missed."
    )

    def __init__(self, output: str | None = None) -> None:
        self.calls = 0
        self.output = output if output is not None else self.DEFAULT_OUTPUT

    async def messages_create(self, **_: Any) -> SimpleNamespace:
        self.calls += 1
        return _content_response(self.output)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_world(db_engine) -> dict[str, Any]:
    """Seed user + stranger + mission + active session + submission row.

    Returns a dict pointing at every row so individual tests can mutate
    state directly (e.g. flip session.status to ``graded``) without
    re-querying.
    """
    from app.config import get_settings
    from app.db import session as session_module
    from app.reports.verification import (
        build_envelope,
        compute_hash,
        compute_signature,
        verify_secret,
    )

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_module.AsyncSessionLocal() as db:
        owner = User(
            id=_OWNER_ID,
            email="wave4-owner@example.com",
            display_name="Wave Four Owner",
            handle="wave4-owner",
            coaching_reflections_enabled=True,
        )
        stranger = User(
            id=_STRANGER_ID,
            email="wave4-stranger@example.com",
            display_name="Stranger",
            handle="wave4-stranger",
        )
        mission = Mission(
            id=_MISSION_ID,
            title="P1-4-5-6 audit mission",
            difficulty="intermediate",
            category="auth",
            repo_pack="fullstack-auth-demo",
            initial_commit="abc123de" * 5,
            estimated_minutes=10,
            failure_mode="cookie_expiry_not_enforced",
            skills_tested=["auth"],
            manifest_sha256="deadbeef" * 8,
            version=1,
            kind="standard",
            published=True,
            expected_weak_dim="safety",
        )
        session_row = SessionRow(
            id=_SESSION_ID,
            user_id=_OWNER_ID,
            mission_id=_MISSION_ID,
            status="active",
            attempt_index=1,
            started_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        db.add_all([owner, stranger, mission, session_row])
        await db.flush()

        submission = Submission(
            id=_SUBMISSION_ID,
            session_id=_SESSION_ID,
            final_diff=(
                "diff --git a/src/session.ts b/src/session.ts\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            ),
            visible_test_results=[],
            hidden_test_results=[],
            validator_results=[],
            score_report={
                "dimensions": {
                    "safety": {"score": 6, "max": 10, "signals": []},
                    "verification": {"score": 8, "max": 10, "signals": []},
                },
                "effective_max": 100,
                "missed_failure_mode": False,
                "total": 78,
            },
            total_score=78,
            score_cap_reason=None,
            created_at=datetime.now(UTC),
            critical_moments=[
                {
                    "kind": "first_failing_test",
                    "summary": "visible test failed",
                    # Intentionally omit file_path / start_line / end_line
                    # so the audit confirms the FE TS shape tolerates that.
                },
                {
                    "kind": "patch_applied",
                    "summary": "patch landed in cookies.ts",
                    "file_path": "src/cookies.ts",
                    "start_line": 12,
                    "end_line": 18,
                },
            ],
        )
        db.add(submission)
        await db.flush()

        # Stamp the verification hash + signature so the replay artefact
        # builder doesn't end up with an empty envelope signature.
        settings = get_settings()
        envelope = build_envelope(
            submission=submission,
            session=session_row,
            manifest=None,
            user=owner,
            mission_row=mission,
        )
        secret = verify_secret(settings)
        h = compute_hash(envelope)
        submission.verification_hash = h
        submission.verification_signature = compute_signature(h, secret)
        await db.commit()

    return {
        "owner": owner,
        "stranger": stranger,
        "mission": mission,
        "session": session_row,
        "submission": submission,
    }


def _owner_cookie(user_id: uuid.UUID = _OWNER_ID) -> str:
    """Build a session cookie for the audit user."""
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


@pytest_asyncio.fixture
async def world(db_engine) -> dict[str, Any]:
    return await _seed_world(db_engine)


# ---------------------------------------------------------------------------
# App factory — overrides require_auth + get_db so we can drive the
# routers directly without minting a real cookie. (The coaching +
# replay tests still use a real cookie so the auth-matrix assertions
# stay honest.)
# ---------------------------------------------------------------------------


def _build_app_with_overrides(*, user: User | None) -> Any:
    """Build a FastAPI app whose require_auth resolves to ``user``.

    When ``user`` is None we leave require_auth alone so anonymous
    callers exercise the production 401 / 404 path.

    The DB override mirrors :func:`app.db.session.get_db` exactly —
    yields a session, commits on clean exit, rolls back on raise — so a
    handler's mutations actually persist to the in-memory engine
    instead of being silently dropped at session-close time. Skipping
    the commit was the root cause of the first audit run's "PUT
    succeeds, GET returns body=''" failure.
    """
    from app.db import session as session_module
    from app.main import create_app
    from app.sessions.events import (
        clear_pending_publishes,
        drain_pending_publishes,
    )

    app = create_app()

    async def _override_db():
        async with session_module.AsyncSessionLocal() as db:
            try:
                yield db
            except Exception:
                await db.rollback()
                clear_pending_publishes(db)
                raise
            else:
                await db.commit()
                await drain_pending_publishes(db)

    app.dependency_overrides[get_db] = _override_db
    if user is not None:
        def _as_user() -> User:
            return user
        app.dependency_overrides[require_auth] = _as_user
    app.state.sandbox_pool = _FakePool()
    return app


# ---------------------------------------------------------------------------
# Cross-cutting helper: rebind the session module + re-load the audit user
# from the test DB inside each test that needs it. We always re-fetch
# because the seeded ``world`` rows are detached from the per-test DB
# session.
# ---------------------------------------------------------------------------


async def _refetch_user(user_id: uuid.UUID) -> User:
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        # Detach so the caller can use the row outside the DB session.
        db.expunge(user)
        return user


# ===========================================================================
# P1-4 — Scratchpad audit
# ===========================================================================


class TestScratchpadAudit:
    """Wave 4 checks 3-11 — every scratchpad endpoint + the coalescing
    window, the give-up gate, and the survive-reset invariant.
    """

    @pytest.mark.asyncio
    async def test_scratchpad_full_flow(self, world) -> None:
        owner = await _refetch_user(_OWNER_ID)
        app = _build_app_with_overrides(user=owner)
        app.state.sandbox_pool.attach(_SESSION_ID, _MISSION_ID)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Check 3 — initial GET returns body=""
            r0 = await ac.get(f"/api/v1/sessions/{_SESSION_ID}/note")
            assert r0.status_code == 200, r0.text
            assert r0.json()["body"] == ""

            # Check 4 — first PUT roundtrips
            r1 = await ac.put(
                f"/api/v1/sessions/{_SESSION_ID}/note",
                json={"body": _NOTE_BODY_INITIAL},
                **_csrf(),
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["body"] == _NOTE_BODY_INITIAL

            # Check 5 — GET reflects PUT
            r2 = await ac.get(f"/api/v1/sessions/{_SESSION_ID}/note")
            assert r2.status_code == 200
            assert r2.json()["body"] == _NOTE_BODY_INITIAL

            # Check 6 — second PUT within 30s coalesces
            r3 = await ac.put(
                f"/api/v1/sessions/{_SESSION_ID}/note",
                json={"body": _NOTE_BODY_UPDATED},
                **_csrf(),
            )
            assert r3.status_code == 200
            assert r3.json()["body"] == _NOTE_BODY_UPDATED

            from app.db import session as session_module

            async with session_module.AsyncSessionLocal() as db:
                events = list(
                    (
                        await db.execute(
                            select(SupervisionEvent)
                            .where(SupervisionEvent.session_id == _SESSION_ID)
                            .where(SupervisionEvent.event_type == "note.edited")
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(events) == 1, [e.payload for e in events]
            assert events[0].payload["bytes"] == len(_NOTE_BODY_UPDATED.encode("utf-8"))

            # Check 7 — back-date and PUT again; a SECOND note.edited event lands.
            far_past = datetime.now(UTC) - timedelta(minutes=5)
            async with session_module.AsyncSessionLocal() as db:
                ev_row = (
                    await db.execute(
                        select(SupervisionEvent)
                        .where(SupervisionEvent.session_id == _SESSION_ID)
                        .where(SupervisionEvent.event_type == "note.edited")
                    )
                ).scalar_one()
                ev_row.occurred_at = far_past
                note_row = (
                    await db.execute(
                        select(SessionNote).where(SessionNote.session_id == _SESSION_ID)
                    )
                ).scalar_one()
                note_row.updated_at = far_past
                await db.commit()

            r4 = await ac.put(
                f"/api/v1/sessions/{_SESSION_ID}/note",
                json={"body": "third edit AFTER the coalesce window expired"},
                **_csrf(),
            )
            assert r4.status_code == 200

            async with session_module.AsyncSessionLocal() as db:
                events = list(
                    (
                        await db.execute(
                            select(SupervisionEvent)
                            .where(SupervisionEvent.session_id == _SESSION_ID)
                            .where(SupervisionEvent.event_type == "note.edited")
                            .order_by(SupervisionEvent.occurred_at.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(events) == 2, [e.payload for e in events]
            assert events[-1].payload["seconds_since_last_edit"] >= 60

            # Check 8 — note.viewed_during_prompt event
            r5 = await ac.post(
                f"/api/v1/sessions/{_SESSION_ID}/events/note-viewed",
                json={"bytes_at_view": 50},
                **_csrf(),
            )
            assert r5.status_code == 204, r5.text
            async with session_module.AsyncSessionLocal() as db:
                viewed = list(
                    (
                        await db.execute(
                            select(SupervisionEvent)
                            .where(SupervisionEvent.session_id == _SESSION_ID)
                            .where(SupervisionEvent.event_type == "note.viewed_during_prompt")
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(viewed) == 1
            assert viewed[0].payload == {"bytes_at_view": 50}

            # Check 9 — oversized body returns 413 + envelope
            huge = "é" * 16_385  # 32,770 bytes — over the 32,768 cap
            r6 = await ac.put(
                f"/api/v1/sessions/{_SESSION_ID}/note",
                json={"body": huge},
                **_csrf(),
            )
            assert r6.status_code == 413, r6.text
            assert r6.json()["detail"]["code"] == "scratchpad_too_large"

            # Check 11 — flip the session to gave_up + verify PUT 409s.
            async with session_module.AsyncSessionLocal() as db:
                row = await db.get(SessionRow, _SESSION_ID)
                row.gave_up_at = datetime.now(UTC)
                row.status = "graded"  # post-give-up terminal state
                await db.commit()

            r7 = await ac.put(
                f"/api/v1/sessions/{_SESSION_ID}/note",
                json={"body": "post-mortem doodles"},
                **_csrf(),
            )
            assert r7.status_code == 409, r7.text
            assert r7.json()["detail"]["code"] == "session_not_active"

            # Check 10 — body survives even after the session is terminal.
            r8 = await ac.get(f"/api/v1/sessions/{_SESSION_ID}/note")
            assert r8.status_code == 200
            assert r8.json()["body"] == "third edit AFTER the coalesce window expired"


# ===========================================================================
# P1-4 — Coaching audit (checks 12-16)
# ===========================================================================


class TestCoachingAudit:
    @pytest.mark.asyncio
    async def test_coaching_full_matrix(self, world, monkeypatch) -> None:
        from app.config import get_settings
        from app.db import session as session_module
        from app.reports.router import issue_share_token

        # Seed a non-empty scratchpad and flip session to graded so the
        # coaching endpoint resolves.
        async with session_module.AsyncSessionLocal() as db:
            db.add(
                SessionNote(
                    session_id=_SESSION_ID,
                    body="check the cookie expiration",
                    updated_at=datetime.now(UTC),
                )
            )
            db.add(
                SupervisionEvent(
                    session_id=_SESSION_ID,
                    event_type="prompt.submitted",
                    payload={"prompt": "fix the bug", "chars": 11},
                    occurred_at=datetime.now(UTC),
                )
            )
            row = await db.get(SessionRow, _SESSION_ID)
            row.status = "graded"
            await db.commit()

        stub = _StubCoachingClient()
        monkeypatch.setattr(coaching_module, "_build_client", lambda: stub)

        app = _build_app_with_overrides(user=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            url = f"/api/v1/submissions/{_SUBMISSION_ID}/coaching"

            # Check 12 — owner GET returns parsed coaching + anchors.
            ac.cookies.set(get_settings().session_cookie_name, _owner_cookie())
            r1 = await ac.get(url)
            assert r1.status_code == 200, r1.text
            payload = r1.json()
            assert isinstance(payload["reflection"], str)
            assert payload["anchored_event_id"] is not None
            assert payload["anchored_note_quote"] == "check the cookie expiration"
            assert payload["cached"] is False

            # Check 13 — second GET hits the cache, LLM not re-called.
            r2 = await ac.get(url)
            assert r2.status_code == 200
            assert r2.json()["cached"] is True
            assert stub.calls == 1

            # Check 14 — opt-out via POST coaching-consent forces reflection=null.
            consent_url = "/api/v1/auth/me/coaching-consent"
            consent_resp = await ac.post(
                consent_url,
                json={"coaching_reflections_enabled": False},
                **_csrf(),
            )
            assert consent_resp.status_code == 200, consent_resp.text
            assert consent_resp.json()["coaching_reflections_enabled"] is False

            r3 = await ac.get(url)
            assert r3.status_code == 200, r3.text
            assert r3.json()["reflection"] is None

            # Check 15 — different user → 403.
            ac.cookies.set(
                get_settings().session_cookie_name,
                _owner_cookie(_STRANGER_ID),
            )
            r4 = await ac.get(url)
            assert r4.status_code == 403

            # Check 16 — share token → 403 (coaching never serves share holders).
            ac.cookies.clear()
            token, _ = issue_share_token(_SUBMISSION_ID, get_settings())
            r5 = await ac.get(url, params={"share": token})
            assert r5.status_code == 403


# ===========================================================================
# P1-6 — Replay audit (checks 17-22)
# ===========================================================================


class TestReplayAudit:
    @pytest.mark.asyncio
    async def test_replay_owner_full(self, world) -> None:
        from app.config import get_settings
        from app.db import session as session_module

        async with session_module.AsyncSessionLocal() as db:
            db.add(
                SessionNote(
                    session_id=_SESSION_ID,
                    body="owner-only scratchpad body",
                    updated_at=datetime.now(UTC),
                )
            )
            db.add(
                SupervisionEvent(
                    session_id=_SESSION_ID,
                    event_type="prompt.submitted",
                    payload={"prompt": "please fix cookie bug", "chars": 21},
                    occurred_at=datetime.now(UTC),
                )
            )
            row = await db.get(SessionRow, _SESSION_ID)
            row.status = "graded"
            await db.commit()

        app = _build_app_with_overrides(user=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            ac.cookies.set(get_settings().session_cookie_name, _owner_cookie())

            # Check 17 — owner sees full payload + scratchpad
            r = await ac.get(f"/api/v1/submissions/{_SUBMISSION_ID}/replay.json")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["submission_id"] == str(_SUBMISSION_ID)
            assert body["scratchpad"]["body"] == "owner-only scratchpad body"
            prompt_ev = next(
                e for e in body["events"] if e["event_type"] == "prompt.submitted"
            )
            assert prompt_ev["payload"].get("prompt") == "please fix cookie bug"

            # Check 22 — ETag stable across calls
            r2 = await ac.get(f"/api/v1/submissions/{_SUBMISSION_ID}/replay.json")
            assert r2.status_code == 200
            assert r.headers["etag"] == r2.headers["etag"]

    @pytest.mark.asyncio
    async def test_replay_share_redacts(self, world) -> None:
        from app.config import get_settings
        from app.db import session as session_module
        from app.reports.router import issue_share_token

        async with session_module.AsyncSessionLocal() as db:
            db.add(
                SessionNote(
                    session_id=_SESSION_ID,
                    body="never shown",
                    updated_at=datetime.now(UTC),
                )
            )
            db.add(
                SupervisionEvent(
                    session_id=_SESSION_ID,
                    event_type="prompt.submitted",
                    payload={"prompt": "secret prompt body", "chars": 18},
                    occurred_at=datetime.now(UTC),
                )
            )
            db.add(
                SupervisionEvent(
                    session_id=_SESSION_ID,
                    event_type="agent.responded",
                    payload={"response": "agent reply body here", "chars": 21},
                    occurred_at=datetime.now(UTC),
                )
            )
            row = await db.get(SessionRow, _SESSION_ID)
            row.status = "graded"
            await db.commit()

        token, _ = issue_share_token(_SUBMISSION_ID, get_settings())
        app = _build_app_with_overrides(user=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Check 18 — share token redacts prompt-bearing payloads and
            # drops the scratchpad entirely.
            r = await ac.get(
                f"/api/v1/submissions/{_SUBMISSION_ID}/replay.json",
                params={"share": token},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert "scratchpad" not in body
            for event in body["events"]:
                if event["event_type"] in PROMPT_BEARING_EVENT_TYPES:
                    assert event["payload"]["redacted"] is True
                    assert isinstance(event["payload"]["byte_count"], int)
                    # Original text is not leaked anywhere.
                    for v in event["payload"].values():
                        assert "secret prompt body" not in str(v)
                        assert "agent reply body here" not in str(v)

    @pytest.mark.asyncio
    async def test_replay_anonymous_404(self, world) -> None:
        from app.db import session as session_module

        async with session_module.AsyncSessionLocal() as db:
            row = await db.get(SessionRow, _SESSION_ID)
            row.status = "graded"
            await db.commit()

        app = _build_app_with_overrides(user=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            ac.cookies.clear()
            r = await ac.get(f"/api/v1/submissions/{_SUBMISSION_ID}/replay.json")
            assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_replay_zip_owner(self, world) -> None:
        from app.config import get_settings
        from app.db import session as session_module

        async with session_module.AsyncSessionLocal() as db:
            row = await db.get(SessionRow, _SESSION_ID)
            row.status = "graded"
            await db.commit()

        app = _build_app_with_overrides(user=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            ac.cookies.set(get_settings().session_cookie_name, _owner_cookie())

            # Check 20 — owner gets a valid zip with all required files.
            r = await ac.get(f"/api/v1/submissions/{_SUBMISSION_ID}/replay.zip")
            assert r.status_code == 200, r.text
            assert r.headers["content-type"].startswith("application/zip")

            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                names = set(zf.namelist())
                # Confirm deterministic order: the zip's namelist is the
                # explicit order the route writes into the buffer.
                assert zf.namelist()[:4] == [
                    "replay.json",
                    "final.diff",
                    "README.md",
                    "verify.html",
                ]
                # Check 21 — replay.json bytes == .json endpoint bytes.
                replay_bytes_from_zip = zf.read("replay.json")
                verify_html = zf.read("verify.html").decode("utf-8")
            required = {"replay.json", "final.diff", "README.md", "verify.html"}
            assert required <= names

            r_json = await ac.get(f"/api/v1/submissions/{_SUBMISSION_ID}/replay.json")
            assert r_json.status_code == 200

            # Check 21 — the zip's replay.json must carry IDENTICAL bytes
            # to the .json endpoint modulo the ``exported_at`` field, which
            # by design is non-deterministic (it is stamped at build time
            # and is explicitly excluded from the canonical signature).
            # We assert signature equality (the real byte-determinism
            # contract) AND structural equality after stripping that one
            # field — anything else would be a spec drift in the
            # canonicalisation pipeline.
            import json as _json

            zip_obj = _json.loads(replay_bytes_from_zip)
            json_obj = _json.loads(r_json.content)
            assert zip_obj["replay_signature"] == json_obj["replay_signature"], (
                "zip and json endpoints must produce the same canonical "
                "signature (which excludes exported_at)"
            )
            zip_obj.pop("exported_at", None)
            json_obj.pop("exported_at", None)
            assert zip_obj == json_obj, (
                "all non-exported_at fields must round-trip identically "
                "between the zip and json endpoints"
            )

            # verify.html is self-contained: no <script> tags, no remote
            # CSS/JS hrefs.
            lower = verify_html.lower()
            assert "<script" not in lower
            assert "https://" not in lower or "href=\"https://" not in lower


# ===========================================================================
# P1-5 — Three-way diff audit (checks 23-24)
# ===========================================================================


class TestThreeWayDiffAudit:
    @pytest.mark.asyncio
    async def test_report_returns_three_diffs_and_critical_moments(
        self, world
    ) -> None:
        from app.config import get_settings
        from app.db import session as session_module

        async with session_module.AsyncSessionLocal() as db:
            row = await db.get(SessionRow, _SESSION_ID)
            row.status = "graded"
            await db.commit()

        app = _build_app_with_overrides(user=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            ac.cookies.set(get_settings().session_cookie_name, _owner_cookie())

            r = await ac.get(f"/api/v1/reports/{_SUBMISSION_ID}")
            assert r.status_code == 200, r.text
            payload = r.json()

            # Check 23 — three-diff trio present on graded submission. The
            # ideal_solution / ideal_solution_diff / agent_patch_diff may
            # be None when the mission folder is absent from the test
            # fixture path, but the keys MUST exist so the FE doesn't
            # branch on `undefined`.
            assert "final_diff" in payload
            assert "ideal_solution_diff" in payload
            assert "agent_patch_diff" in payload
            assert payload["final_diff"].startswith("diff --git")

            # Check 24 — critical_moments present + the FE-optional
            # file_path/start_line/end_line are tolerated (the first
            # moment lacks them; FE has `?: string | null` so no BE
            # change needed).
            cm = payload["critical_moments"]
            assert isinstance(cm, list) and len(cm) == 2
            assert "file_path" not in cm[0] or cm[0].get("file_path") in (None,)
            assert cm[1]["file_path"] == "src/cookies.ts"
