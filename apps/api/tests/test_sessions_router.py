"""GET /sessions/{id} error contract.

Specifically guards the missing-mission branch — when a session row exists
but its mission row has been deleted out from under it, the endpoint must
return 404 (P1-B2). It used to return 500, which framed an unrecoverable
data-integrity error to the FE; in reality the FE just needs a clean
"resource not available" signal so it can route the user back to the
missions index.
"""

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
async def _seeded(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"missing-mission-{uuid.uuid4().hex[:8]}@example.com",
        display_name="MissingMission",
    )
    mission = Mission(
        id="will-be-deleted",
        title="To be deleted",
        difficulty="intermediate",
        category="auth",
        repo_pack="fullstack-auth-demo",
        initial_commit="abc12345",
        estimated_minutes=10,
        failure_mode="x",
        skills_tested=["auth"],
        manifest_sha256="c" * 64,
        version=1,
        published=True,
    )
    db_session.add_all([user, mission])
    await db_session.flush()
    session = SessionRow(
        id=uuid.uuid4(),
        user_id=user.id,
        mission_id=mission.id,
        status="active",
        started_at=datetime.now(UTC),
    )
    db_session.add(session)
    await db_session.commit()
    return {"user": user, "mission": mission, "session": session}


@pytest.mark.asyncio
async def test_get_session_returns_404_when_mission_deleted(_seeded, db_session) -> None:
    """Delete the mission row and confirm the endpoint now 404s rather than 500."""
    app = create_app()

    async def _override_db():
        yield db_session

    def _as_owner() -> User:
        return _seeded["user"]

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _as_owner

    # Drop the mission row but leave the session pointing at it. SQLite in
    # tests doesn't enforce the FK at delete time, which lets us simulate the
    # "mission deleted under an active session" race in a self-contained way.
    await db_session.delete(_seeded["mission"])
    await db_session.commit()

    sid = _seeded["session"].id
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get(f"/api/v1/sessions/{sid}")

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Mission not found for session"
