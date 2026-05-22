"""POST /sessions/{id}/submit + GET /sessions/{id}/submission round-trip."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User
from app.sandbox.types import GradingArtifacts, SandboxHandle


class _FakeDriver:
    name = "local"

    async def freeze_and_grade(self, handle, mission, *, manifest_folder=None) -> GradingArtifacts:
        return GradingArtifacts(
            diff="--- a/x\n+++ b/x\n@@ -1,1 +1,2 @@\n a\n+b\n",
            test_results={
                "unit": {
                    "suite": "unit",
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "timed_out": False,
                },
                "hidden": {
                    "suite": "hidden",
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": 4,
                    "failed": 0,
                    "skipped": 0,
                    "timed_out": False,
                },
            },
            logs={},
        )

    async def read_file(self, handle, path):
        return b""


class _FakePool:
    """Stand-in for SandboxPool used by the submit endpoint."""

    def __init__(self, handle: SandboxHandle) -> None:
        self.driver = _FakeDriver()
        self._handles = [handle]

    def handles_snapshot(self) -> list[SandboxHandle]:
        return list(self._handles)


@pytest.mark.asyncio
async def test_submit_endpoint_round_trip(client, db_engine, monkeypatch) -> None:
    # Share the test engine with the app's session module.
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed user + mission + session.
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=user_id, email="user@arena.local", display_name="U"))
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="x",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="active",
            )
        )
        await db.commit()

    # Issue a real session cookie so require_auth succeeds.
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from app.auth.session_cookie import _ALGORITHM
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )
    client.cookies.set(settings.session_cookie_name, token)

    app = client._transport.app  # type: ignore[attr-defined]

    # Inject the fake sandbox pool.
    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="auth-cookie-expiration",
        session_id=session_id,
    )
    app.state.sandbox_pool = _FakePool(handle)

    # POST submit. Double-submit CSRF: cookie value == header value.
    csrf = "x" * 64
    client.cookies.set("arena_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/submit",
        headers=headers,
    )
    # Submit returns 200 with the final SubmissionRead synchronously — the
    # previous 202 lied about asynchrony since the call blocks on the grader.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == str(session_id)
    assert body["total_score"] >= 0
    assert "score_report" in body
    submission_id = body["id"]

    # GET submission.
    resp2 = await client.get(f"/api/v1/sessions/{session_id}/submission")
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["id"] == submission_id

    # Ownership: a stranger gets 403.
    other_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=other_id, email="other@arena.local", display_name="O"))
        await db.commit()

    stranger_token = jwt.encode(
        {
            "sub": str(other_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )
    client.cookies.set(settings.session_cookie_name, stranger_token)
    resp3 = await client.get(f"/api/v1/sessions/{session_id}/submission")
    assert resp3.status_code == 403


@pytest.mark.asyncio
async def test_submit_endpoint_409_when_already_graded(client, db_engine, monkeypatch) -> None:
    """Re-submitting a graded session should be rejected with 409."""
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=user_id, email="g@arena.local", display_name="G"))
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="x",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="graded",
            )
        )
        await db.commit()

    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from app.auth.session_cookie import _ALGORITHM
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )
    client.cookies.set(settings.session_cookie_name, token)

    csrf = "x" * 64
    client.cookies.set("arena_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/submit",
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "already" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_submit_endpoint_503_when_no_sandbox(client, db_engine, monkeypatch) -> None:
    """Submitting before the sandbox provisions should return 503."""
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=user_id, email="n@arena.local", display_name="N"))
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="x",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="active",
            )
        )
        await db.commit()

    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from app.auth.session_cookie import _ALGORITHM
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )
    client.cookies.set(settings.session_cookie_name, token)

    app = client._transport.app  # type: ignore[attr-defined]
    # Empty pool — no handle for this session id.
    app.state.sandbox_pool = _FakePool(
        SandboxHandle(
            id="other",
            driver="local",
            workdir=Path("/tmp/arena-fake"),
            mission_id="auth-cookie-expiration",
            session_id=uuid.uuid4(),  # different session id
        )
    )

    csrf = "x" * 64
    client.cookies.set("arena_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/submit",
        headers=headers,
    )
    assert resp.status_code == 503, resp.text
    assert "sandbox" in resp.json()["detail"].lower()
