"""Race-condition test for ``AgentService.respond`` (P0-1).

Two concurrent ``respond`` calls on the same session used to read
``session.agent_turns`` independently and then both insert an ``AgentTurn``
row with the same ``turn_index`` — violating ``UNIQUE(session_id,
turn_index)`` and surfacing as an ``IntegrityError`` for one of the callers.

The fix swaps the read-then-write to an atomic ``UPDATE ... RETURNING`` so
each caller claims a distinct next index. This test pins that behaviour by
firing two ``respond`` coroutines through ``asyncio.gather`` against a shared
in-memory SQLite database and asserting both succeed with distinct indices.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.service import AgentService, clear_agent_caches
from app.db.base import Base
from app.models.agent_turn import AgentTurn
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User
from app.schemas.session import ContextSelection
from app.sessions.events import EventEmitter


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    clear_agent_caches()
    yield
    clear_agent_caches()


@pytest.mark.asyncio
async def test_concurrent_respond_yields_distinct_turn_indices(
    repo_root: Path,
) -> None:
    """Two simultaneous ``respond`` calls must each get a unique turn_index."""
    # Fresh in-memory engine — isolated from the shared ``db_engine`` fixture
    # so we can drive concurrent sessions without cross-contamination.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)
    from tests.conftest import _patch_models_for_sqlite

    _patch_models_for_sqlite()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    mission_id = "auth-cookie-expiration"

    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email=f"race-{uuid.uuid4().hex[:8]}@test.local",
                display_name="Race",
            )
        )
        db.add(
            Mission(
                id=mission_id,
                title="Race condition mission",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="abc123de",
                estimated_minutes=20,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
            )
        )
        await db.flush()
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id=mission_id,
                status="active",
                agent_turns=0,
            )
        )
        await db.commit()

    # Resolve the mission folder + manifest from disk so we exercise the real
    # template / classifier path. Same loader the API uses.
    from app.missions.loader import MissionLoader

    missions_root = repo_root / "missions"
    loader = MissionLoader(missions_root)
    loaded = next(m for m in loader.scan() if m.manifest.id == mission_id)
    mission_folder = loaded.folder
    manifest = loaded

    service = AgentService()

    async def _one_call(prompt_suffix: str) -> int:
        async with SessionLocal() as db:
            session = (
                await db.execute(select(SessionRow).where(SessionRow.id == session_id))
            ).scalar_one()
            emitter = EventEmitter(db=db)
            resp = await service.respond(
                db=db,
                session=session,
                prompt=f"Please fix the expired-cookie bug {prompt_suffix}",
                context=ContextSelection(),
                mission_folder=mission_folder,
                manifest=manifest,
                emitter=emitter,
            )
            await db.commit()
            return resp.turn_index

    # asyncio.gather schedules both coroutines on the same loop. SQLite's
    # default journaling will serialise the two UPDATEs, but the *test* is
    # about the contract of the service: both calls must succeed and the
    # turn_index values must be distinct.
    idx_a, idx_b = await asyncio.gather(_one_call("A"), _one_call("B"))
    assert {idx_a, idx_b} == {0, 1}, (idx_a, idx_b)

    # Verify two AgentTurn rows landed with distinct turn_index values.
    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(AgentTurn)
                    .where(AgentTurn.session_id == session_id)
                    .order_by(AgentTurn.turn_index)
                )
            )
            .scalars()
            .all()
        )
        assert [r.turn_index for r in rows] == [0, 1]

        session_row = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one()
        assert session_row.agent_turns == 2

    await engine.dispose()
