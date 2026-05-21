"""Tests for ``AgentService.apply_patch`` and the ``/patches/.../apply`` route.

The happy path runs against the canonical Mission 01 ``agent_patch.diff``;
the failure path is exercised with a stub sandbox driver that reports a
non-zero exit so we can assert ``patch.failed`` event behaviour.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.service import AgentService, clear_agent_caches
from app.db.base import Base
from app.models.agent_turn import AgentTurn
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.sandbox.types import ApplyResult


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    clear_agent_caches()
    yield
    clear_agent_caches()


async def _bind_engine(db_engine: Any):
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(  # type: ignore[assignment]
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_module


async def _seed(db_engine: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert a mission, user, session, and a pre-existing AgentTurn row."""
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        user = User(
            email=f"apply-{uuid.uuid4().hex[:8]}@test.local",
            display_name="Apply",
        )
        db.add(user)
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Expired Session Cookie Still Grants Access",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="abc123de",
                estimated_minutes=35,
                failure_mode="checks_presence_not_expiration",
                skills_tested=["auth"],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
            )
        )
        await db.flush()
        session = SessionRow(
            user_id=user.id,
            mission_id="auth-cookie-expiration",
            status="active",
        )
        db.add(session)
        await db.flush()
        turn = AgentTurn(
            session_id=session.id,
            turn_index=0,
            user_prompt="fix the bug",
            selected_context={"files": [], "logs": [], "tests": [], "extras": []},
            agent_response="seed",
        )
        db.add(turn)
        await db.commit()
        return user.id, session.id, turn.id


class _FakeEmitter:
    """In-memory event sink mirroring the EventEmitter contract."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(
        self, session_id: uuid.UUID, event_type: str, payload: dict[str, Any]
    ) -> None:
        self.events.append((event_type, payload))


class _OkDriver:
    """Sandbox driver that reports a successful apply."""

    async def apply_diff(self, _handle: Any, _patch: str) -> ApplyResult:
        return ApplyResult(
            applied=True,
            files_changed=["backend/src/middleware/requireAuth.ts"],
            added_lines=3,
            removed_lines=1,
        )


class _FailingDriver:
    async def apply_diff(self, _handle: Any, _patch: str) -> ApplyResult:
        return ApplyResult(
            applied=False,
            files_changed=[],
            error="error: corrupt patch at line 3",
        )


class _RaisingDriver:
    async def apply_diff(self, _handle: Any, _patch: str) -> ApplyResult:
        raise RuntimeError("boom: sandbox vanished")


@pytest.mark.asyncio
async def test_apply_patch_success_emits_patch_applied(db_engine: Any) -> None:
    await _bind_engine(db_engine)
    _, session_id, turn_id = await _seed(db_engine)

    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        emitter = _FakeEmitter()
        service = AgentService()
        result = await service.apply_patch(
            db=db,
            session=session,
            turn_id=turn_id,
            sandbox_driver=_OkDriver(),
            sandbox_handle=object(),
            emitter=emitter,
        )
        await db.commit()

    assert result.applied is True
    assert result.files_changed == ["backend/src/middleware/requireAuth.ts"]
    assert result.added_lines == 3
    assert any(e[0] == "patch.applied" for e in emitter.events)

    # Persisted patch text on the turn row.
    async with session_module.AsyncSessionLocal() as db:
        turn = (
            await db.execute(select(AgentTurn).where(AgentTurn.id == turn_id))
        ).scalar_one()
        assert turn.applied_patch is not None
        assert turn.patch_applied_at is not None


@pytest.mark.asyncio
async def test_apply_patch_failure_emits_patch_failed(db_engine: Any) -> None:
    await _bind_engine(db_engine)
    _, session_id, turn_id = await _seed(db_engine)

    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        emitter = _FakeEmitter()
        service = AgentService()
        result = await service.apply_patch(
            db=db,
            session=session,
            turn_id=turn_id,
            sandbox_driver=_FailingDriver(),
            sandbox_handle=object(),
            emitter=emitter,
        )
        await db.commit()

    assert result.applied is False
    assert "corrupt patch" in (result.error or "")
    fail_events = [e for e in emitter.events if e[0] == "patch.failed"]
    assert fail_events, emitter.events
    assert "corrupt patch" in fail_events[0][1]["error"]


@pytest.mark.asyncio
async def test_apply_patch_driver_exception_emits_patch_failed(db_engine: Any) -> None:
    """An exception from the driver is converted to PatchResult + ``patch.failed``."""
    await _bind_engine(db_engine)
    _, session_id, turn_id = await _seed(db_engine)

    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        emitter = _FakeEmitter()
        service = AgentService()
        result = await service.apply_patch(
            db=db,
            session=session,
            turn_id=turn_id,
            sandbox_driver=_RaisingDriver(),
            sandbox_handle=object(),
            emitter=emitter,
        )
        await db.commit()

    assert result.applied is False
    assert "boom" in (result.error or "")
    assert any(e[0] == "patch.failed" for e in emitter.events)


@pytest.mark.asyncio
async def test_apply_patch_failure_event_persists_via_emitter(
    db_engine: Any,
) -> None:
    """Sanity: when using the real EventEmitter, patch.failed reaches the DB."""
    from app.sessions.events import EventEmitter

    await _bind_engine(db_engine)
    _, session_id, turn_id = await _seed(db_engine)

    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        session = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        service = AgentService()
        result = await service.apply_patch(
            db=db,
            session=session,
            turn_id=turn_id,
            sandbox_driver=_FailingDriver(),
            sandbox_handle=object(),
            emitter=EventEmitter(db=db, redis_client=None),
        )
        await db.commit()

    assert result.applied is False

    async with session_module.AsyncSessionLocal() as db:
        types = [
            e.event_type
            for e in (
                await db.execute(
                    select(SupervisionEvent).where(
                        SupervisionEvent.session_id == session_id
                    )
                )
            )
            .scalars()
            .all()
        ]
        assert "patch.failed" in types
