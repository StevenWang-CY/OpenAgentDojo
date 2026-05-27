"""Submit pipeline TimeoutError must persist ``session.status='error'``.

Previously the timeout fallback wrote a Submission stub (so GET /submission
returns 200) but never explicitly flipped the session row to a terminal
state. The fallback's internal ``rollback`` then discarded any in-flight
status mutation, leaving the session stuck in ``submitting`` until the
orphan sweeper eventually noticed — 15 minutes later by default.

This test pins the new contract: before ``_ensure_failed_stub`` runs we
flip + commit ``status='error'`` and stamp ``completed_at`` so the FE can
surface the failure immediately.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User


class _TimingOutRunner:
    """Stand-in for :class:`GradingRunner` that always TimeoutErrors."""

    def __init__(self, _settings) -> None:
        self.calls = 0

    async def run_and_persist(self, **_kwargs):
        self.calls += 1
        raise TimeoutError("grading exceeded 30s budget (synthetic)")


class _FakeDriver:
    name = "local"


class _FakePool:
    def __init__(self, handle) -> None:
        self.driver = _FakeDriver()
        self._handle = handle

    def handle_for(self, _sid):
        return self._handle

    def handles_snapshot(self):
        return [self._handle]


@pytest.mark.asyncio
async def test_submit_timeout_marks_session_errored(db_engine, monkeypatch) -> None:
    from fastapi import HTTPException

    from app.db import session as session_module
    from app.sandbox.types import SandboxHandle
    from app.sessions import submit as submit_module

    # Share the test engine.
    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(  # type: ignore[assignment]
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    mission_id = "auth-cookie-expiration"  # real folder under repo /missions
    session_id = uuid.uuid4()

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(id=user_id, email=f"tmout-{user_id}@example.com", display_name="T"))
        db.add(
            Mission(
                id=mission_id,
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
                expected_weak_dim="safety",
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id=mission_id,
                status="active",
            )
        )
        await db.commit()

    # Patch the runner ctor in the submit module so submit_session uses our stub.
    monkeypatch.setattr(submit_module, "GradingRunner", _TimingOutRunner)

    handle = SandboxHandle(
        id="h-timeout",
        driver="local",
        workdir=Path("/tmp/arena-timeout"),
        mission_id=mission_id,
        session_id=session_id,
    )
    fake_app = SimpleNamespace(state=SimpleNamespace(sandbox_pool=_FakePool(handle)))
    fake_request = SimpleNamespace(app=fake_app)

    async with factory() as db:
        session = await db.get(SessionRow, session_id)
        assert session is not None
        with pytest.raises(HTTPException) as ei:
            await submit_module.submit_session(db, session, fake_request)  # type: ignore[arg-type]
        # The route handler re-raises 504 on timeout so the FE retry path
        # triggers — confirms the original exception is NOT swallowed.
        assert ei.value.status_code == 504

    # In a separate session, re-read the row and confirm the terminal mutation
    # was persisted (the stub-writer's rollback must NOT have lost it).
    async with factory() as db:
        row = await db.get(SessionRow, session_id)
        assert row is not None
        assert row.status == "error", f"expected 'error', got {row.status!r}"
        assert row.completed_at is not None, "completed_at must be stamped"
