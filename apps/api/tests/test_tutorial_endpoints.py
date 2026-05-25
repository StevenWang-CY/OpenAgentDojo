"""P0-1 — tutorial-step + tutorial replay endpoints.

* ``POST /sessions/{id}/events/tutorial-step`` writes a supervision
  event with the correct ``event_type`` ("tutorial.step_completed" or
  "tutorial.dismissed") and step_id payload.
* ``POST /auth/me/tutorial/replay`` clears ``tutorial_completed_at`` and
  bumps ``tutorial_replay_count``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
from app.models.supervision_event import SupervisionEvent
from app.models.user import User


@pytest_asyncio.fixture
async def tutorial_setup(db_session: AsyncSession):
    user = User(
        email=f"u{uuid.uuid4().hex[:8]}@example.com",
        handle=f"u{uuid.uuid4().hex[:8]}",
        tutorial_completed_at=datetime.now(UTC),
        tutorial_replay_count=2,
    )
    mission = Mission(
        id="orientation",
        title="Orientation",
        difficulty="beginner",
        category="tutorial",
        repo_pack="fullstack-auth-demo",
        initial_commit="HEAD",
        estimated_minutes=8,
        failure_mode="missing_argument_concatenation",
        skills_tested=["workflow"],
        manifest_sha256="0" * 64,
        version=1,
        published=False,
        kind="tutorial",
    )
    db_session.add_all([user, mission])
    await db_session.flush()

    session = SessionRow(
        user_id=user.id,
        mission_id="orientation",
        status="active",
    )
    db_session.add(session)
    await db_session.commit()
    return {"user": user, "session": session}


_CSRF = "test-csrf-token-fixed-value"


def _make_app(db_session: AsyncSession, user: User):
    app = create_app()

    async def _override_db():
        yield db_session

    def _as_user() -> User:
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _as_user
    return app


def _csrf_kwargs() -> dict:
    return {
        "headers": {"X-CSRF-Token": _CSRF},
        "cookies": {"arena_csrf": _CSRF},
    }


@pytest.mark.asyncio
async def test_tutorial_step_event_persists(tutorial_setup, db_session: AsyncSession) -> None:
    user = tutorial_setup["user"]
    session = tutorial_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            f"/api/v1/sessions/{session.id}/events/tutorial-step",
            json={"step_id": "select-context", "action": "completed"},
            **_csrf_kwargs(),
        )
    assert resp.status_code == 204

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent).where(SupervisionEvent.session_id == session.id)
            )
        )
        .scalars()
        .all()
    )
    types = [e.event_type for e in events]
    assert "tutorial.step_completed" in types
    payload = next(e.payload for e in events if e.event_type == "tutorial.step_completed")
    assert payload["step_id"] == "select-context"
    assert payload["mission_id"] == "orientation"


@pytest.mark.asyncio
async def test_tutorial_step_dismissed_emits_dismissed_event(
    tutorial_setup, db_session: AsyncSession
) -> None:
    user = tutorial_setup["user"]
    session = tutorial_setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            f"/api/v1/sessions/{session.id}/events/tutorial-step",
            json={"step_id": "verify-with-tests", "action": "dismissed"},
            **_csrf_kwargs(),
        )
    assert resp.status_code == 204
    events = (
        (
            await db_session.execute(
                select(SupervisionEvent).where(SupervisionEvent.session_id == session.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(e.event_type == "tutorial.dismissed" for e in events)


@pytest.mark.asyncio
async def test_tutorial_replay_clears_completion_and_bumps_count(
    tutorial_setup, db_session: AsyncSession
) -> None:
    user = tutorial_setup["user"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/api/v1/auth/me/tutorial/replay",
            **_csrf_kwargs(),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tutorial_completed_at"] is None
    assert body["tutorial_replay_count"] == 3

    refreshed = (await db_session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert refreshed.tutorial_completed_at is None
    assert refreshed.tutorial_replay_count == 3


@pytest.mark.asyncio
async def test_tutorial_replay_increment_is_atomic(
    tutorial_setup, db_session: AsyncSession
) -> None:
    """Concurrent replays must not lose increments.

    The previous Python-side read-modify-write lost increments under
    concurrent calls (both readers saw N, both wrote N+1). The fix uses
    a single ``UPDATE ... SET tutorial_replay_count = User.tutorial_replay_count + 1``
    so the counter advances atomically. We replay three times back-to-
    back and assert the counter advances by exactly three.
    """
    user = tutorial_setup["user"]
    initial = user.tutorial_replay_count
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        for _ in range(3):
            resp = await ac.post(
                "/api/v1/auth/me/tutorial/replay",
                **_csrf_kwargs(),
            )
            assert resp.status_code == 200
    refreshed = (await db_session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert refreshed.tutorial_replay_count == initial + 3
    assert refreshed.tutorial_completed_at is None
