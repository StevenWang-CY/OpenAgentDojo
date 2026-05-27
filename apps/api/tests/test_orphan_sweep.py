"""Orphan sweeper flips active DB rows with no live pool handle to abandoned.

We seed two ``sessions`` rows — one whose id is present in the pool snapshot,
one whose id is not. After ``_sweep_orphans_once`` the orphan must be
``abandoned`` with a ``completed_at`` stamp, and the live one must be untouched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.sandbox.driver import SandboxDriver
from app.sandbox.pool import SandboxPool
from app.sandbox.types import (
    ApplyResult,
    FileTreeNode,
    GradingArtifacts,
    RunResult,
    SandboxHandle,
)


class _NullDriver(SandboxDriver):
    name = "null"

    async def provision(self, mission, session_id):  # type: ignore[override]
        return SandboxHandle(
            id=str(uuid.uuid4()),
            driver=self.name,
            workdir=Path("/tmp"),
            mission_id="fake",
            session_id=session_id,
        )

    async def attach_shell(self, handle):  # type: ignore[override]
        return None

    async def read_file(self, handle, path):  # type: ignore[override]
        return b""

    async def write_file(self, handle, path, content):  # type: ignore[override]
        return None

    async def list_tree(self, handle, root="/workspace"):  # type: ignore[override]
        return FileTreeNode(path=root, kind="dir")

    async def diff_from_initial(self, handle):  # type: ignore[override]
        return ""

    async def run(self, handle, cmd, timeout_s=60, cwd=None):  # type: ignore[override]
        return RunResult(exit_code=0, stdout="", stderr="", duration_ms=0)

    async def apply_diff(self, handle, diff_text):  # type: ignore[override]
        return ApplyResult(applied=True)

    async def freeze_and_grade(self, handle, mission):  # type: ignore[override]
        return GradingArtifacts(diff="", test_results={}, logs={})

    async def destroy(self, handle):  # type: ignore[override]
        return None

    async def spawn_lsp(self, handle, language):  # type: ignore[override]
        # P1-3 — orphan-sweep tests don't exercise the LSP surface; raise
        # the typed unavailable error so the abstract contract is satisfied.
        from app.sandbox.lsp import LSPUnavailableError

        raise LSPUnavailableError("binary_not_found", language)


@pytest.mark.asyncio
async def test_orphan_sweep_marks_orphans_abandoned(db_engine, monkeypatch) -> None:
    from app.config import Settings
    from app.models.mission import Mission
    from app.models.session import SessionRow
    from app.models.user import User

    # Wire AsyncSessionLocal (used inside the sweeper) to the test engine.
    local_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    import app.db.session as db_session_mod

    monkeypatch.setattr(db_session_mod, "AsyncSessionLocal", local_factory)

    # Seed a user and mission so the FKs on sessions resolve.
    async with local_factory() as db:
        user = User(email=f"orphan-{uuid.uuid4().hex}@arena.test", display_name="Orph")
        db.add(user)
        mission = Mission(
            id="fake-orphan-mission",
            version=1,
            title="Fake Orphan",
            difficulty="beginner",
            category="testing",
            repo_pack="__no_such_pack__",
            initial_commit="deadbeef",
            estimated_minutes=5,
            failure_mode="test",
            skills_tested=[],
            manifest_sha256="0" * 64,
            published=True,
            expected_weak_dim="safety",
        )
        db.add(mission)
        await db.commit()

        # Live session — pool handle exists for this one.
        live = SessionRow(
            id=uuid.uuid4(),
            user_id=user.id,
            mission_id=mission.id,
            status="active",
            started_at=datetime.now(UTC),
            last_activity_at=datetime.now(UTC),
        )
        # Orphan session — no pool handle.
        orphan = SessionRow(
            id=uuid.uuid4(),
            user_id=user.id,
            mission_id=mission.id,
            status="active",
            started_at=datetime.now(UTC),
            last_activity_at=datetime.now(UTC),
        )
        db.add_all([live, orphan])
        await db.commit()

        live_id = live.id
        orphan_id = orphan.id

    # Build a pool whose snapshot contains only the live session id.
    pool = SandboxPool(settings=Settings(), driver=_NullDriver())
    # Inject a fake handle straight into the pool's internal map.
    fake_handle = SandboxHandle(
        id="live-handle",
        driver="null",
        workdir=Path("/tmp"),
        mission_id="fake-orphan-mission",
        session_id=live_id,
    )
    pool._handles[fake_handle.id] = fake_handle

    swept = await pool._sweep_orphans_once()
    assert swept == 1

    async with local_factory() as db:
        rows = (await db.execute(select(SessionRow))).scalars().all()
        by_id = {r.id: r for r in rows}
        assert by_id[live_id].status == "active", "live session must stay active"
        assert by_id[orphan_id].status == "abandoned"
        assert by_id[orphan_id].completed_at is not None
