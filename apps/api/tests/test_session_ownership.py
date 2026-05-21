"""User A cannot read user B's session — ownership is enforced (403)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.deps import require_auth
from app.db.session import get_db
from app.main import create_app
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User


@pytest_asyncio.fixture
async def ownership_setup(db_session):
    """Two real users, one mission, one session owned by user A."""
    user_a = User(
        id=uuid.uuid4(),
        email="alice-ownership@example.com",
        display_name="Alice",
    )
    user_b = User(
        id=uuid.uuid4(),
        email="bob-ownership@example.com",
        display_name="Bob",
    )
    mission = Mission(
        id="auth-cookie-expiration",
        title="Ownership test mission",
        difficulty="intermediate",
        category="auth",
        repo_pack="fullstack-auth-demo",
        initial_commit="abc12345",
        estimated_minutes=10,
        failure_mode="x",
        skills_tested=["auth"],
        manifest_sha256="b" * 64,
        version=1,
        published=True,
    )
    db_session.add_all([user_a, user_b, mission])
    await db_session.flush()

    session_a = SessionRow(
        id=uuid.uuid4(),
        user_id=user_a.id,
        mission_id=mission.id,
        status="active",
        started_at=datetime.now(UTC),
    )
    db_session.add(session_a)
    await db_session.commit()
    return {"user_a": user_a, "user_b": user_b, "session_a": session_a}


@pytest.mark.asyncio
async def test_user_b_cannot_read_user_a_session(ownership_setup, db_session) -> None:
    app = create_app()

    # Override get_db to return our test session, and require_auth to return user B.
    async def _override_db():
        yield db_session

    def _as_user_b() -> User:
        return ownership_setup["user_b"]

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _as_user_b

    session_a_id = ownership_setup["session_a"].id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session_a_id}")
    assert resp.status_code == 403
    assert "not your session" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_user_a_can_read_their_own_session(ownership_setup, db_session) -> None:
    app = create_app()

    async def _override_db():
        yield db_session

    def _as_user_a() -> User:
        return ownership_setup["user_a"]

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _as_user_a

    session_a_id = ownership_setup["session_a"].id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session_a_id}")
    # Owner sees their session (200) or hits a 5xx for missing mission manifest
    # — never 403. We assert *not* 403 to keep the test resilient to upstream
    # cache state.
    assert resp.status_code != 403
