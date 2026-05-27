"""End-to-end test for ``POST /sessions/{id}/prompts`` — the agent pipeline.

Covers the happy path:

* the route returns ``AgentTurnResponse``
* a row is persisted to ``agent_turns``
* supervision events ``prompt.submitted`` + ``agent.responded`` are emitted
* ``patch.proposed`` fires when the classified intent is ``fix`` AND the
  mission has an agent patch file on disk
* ``session.agent_turns`` is incremented
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.service import clear_agent_caches
from app.db.base import Base
from app.models.agent_turn import AgentTurn
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    clear_agent_caches()
    yield
    clear_agent_caches()


async def _bind_app_engine_to(db_engine: Any) -> Any:
    """Re-bind the API's session factory to the test engine."""
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(  # type: ignore[assignment]
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_module


async def _seed_mission_user_session(
    db_engine: Any, mission_id: str = "auth-cookie-expiration"
) -> tuple[uuid.UUID, uuid.UUID]:
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        user = User(
            email=f"pipeline-{uuid.uuid4().hex[:8]}@test.local",
            display_name="Pipeline",
        )
        db.add(user)
        db.add(
            Mission(
                id=mission_id,
                title="Expired Session Cookie Still Grants Access",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="abc123de",
                estimated_minutes=35,
                failure_mode="checks_presence_not_expiration",
                skills_tested=["auth", "security"],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        await db.flush()
        session = SessionRow(
            user_id=user.id,
            mission_id=mission_id,
            status="active",
        )
        db.add(session)
        await db.commit()
        return user.id, session.id


def _override_auth(app: Any, user_id: uuid.UUID) -> None:
    """Bypass ``require_auth`` by returning a stub user with the right id."""
    from app.auth.deps import require_auth

    async def _stub() -> Any:
        class _U:
            pass

        u = _U()
        u.id = user_id
        return u

    app.dependency_overrides[require_auth] = _stub


def _csrf_kwargs() -> dict[str, Any]:
    """CSRF double-submit cookie + header pair (any matching value works)."""
    token = "test-csrf-token"
    return {
        "headers": {"X-CSRF-Token": token},
        "cookies": {"arena_csrf": token},
    }


@pytest.mark.asyncio
async def test_post_prompt_returns_response_and_emits_events(client, db_engine) -> None:
    """Happy path: fix-intent prompt → AgentTurn row + 3 events."""
    await _bind_app_engine_to(db_engine)
    user_id, session_id = await _seed_mission_user_session(db_engine)
    _override_auth(client._transport.app, user_id)  # type: ignore[attr-defined]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/prompts",
        json={
            "text": "Please fix the expired-cookie bug in requireAuth.",
            "context": {"files": ["backend/src/middleware/requireAuth.ts"]},
        },
        **_csrf_kwargs(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == str(session_id)
    assert body["turn_index"] == 0
    assert body["agent_response"]
    assert body["proposed_actions"] == ["apply_patch"]

    # Verify DB rows.
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        turn = (
            await db.execute(select(AgentTurn).where(AgentTurn.session_id == session_id))
        ).scalar_one()
        assert turn.user_prompt.startswith("Please fix")
        assert turn.applied_patch is None

        events = (
            (
                await db.execute(
                    select(SupervisionEvent)
                    .where(SupervisionEvent.session_id == session_id)
                    .order_by(SupervisionEvent.id)
                )
            )
            .scalars()
            .all()
        )
        types = [e.event_type for e in events]
        assert "prompt.submitted" in types
        assert "agent.responded" in types
        assert "patch.proposed" in types

        responded = next(e for e in events if e.event_type == "agent.responded")
        assert responded.payload["intent"] == "fix"
        assert responded.payload["proposed_actions"] == ["apply_patch"]

        proposed = next(e for e in events if e.event_type == "patch.proposed")
        assert proposed.payload["turn_id"] == str(turn.id)
        assert proposed.payload["intent"] == "fix"

        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        assert session.agent_turns == 1


@pytest.mark.asyncio
async def test_post_prompt_unknown_intent_skips_patch_proposed(client, db_engine) -> None:
    """Non-fix intents do NOT emit ``patch.proposed``."""
    await _bind_app_engine_to(db_engine)
    user_id, session_id = await _seed_mission_user_session(db_engine)
    _override_auth(client._transport.app, user_id)  # type: ignore[attr-defined]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/prompts",
        json={"text": "what does this codebase do, broadly?"},
        **_csrf_kwargs(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["proposed_actions"] == []

    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        types = [
            e.event_type
            for e in (
                await db.execute(
                    select(SupervisionEvent).where(SupervisionEvent.session_id == session_id)
                )
            )
            .scalars()
            .all()
        ]
        assert "prompt.submitted" in types
        assert "agent.responded" in types
        assert "patch.proposed" not in types


@pytest.mark.asyncio
async def test_post_prompt_requires_ownership(client, db_engine) -> None:
    """A different user calling against another user's session gets 403."""
    await _bind_app_engine_to(db_engine)
    _user_id, session_id = await _seed_mission_user_session(db_engine)
    # Override auth with an UNRELATED user id.
    _override_auth(client._transport.app, uuid.uuid4())  # type: ignore[attr-defined]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/prompts",
        json={"text": "fix the bug"},
        **_csrf_kwargs(),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "forbidden"
