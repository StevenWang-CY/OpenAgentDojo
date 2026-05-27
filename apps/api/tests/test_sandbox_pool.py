"""SandboxPool concurrency + reaper tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.sandbox.driver import SandboxDriver
from app.sandbox.pool import SandboxPool
from app.sandbox.types import (
    ApplyResult,
    FileTreeNode,
    GradingArtifacts,
    RunResult,
    SandboxHandle,
)


class _FakeDriver(SandboxDriver):
    name = "fake"

    def __init__(self) -> None:
        self.provisioned: list[SandboxHandle] = []
        self.destroyed: list[str] = []

    async def provision(self, mission, session_id):  # type: ignore[override]
        h = SandboxHandle(
            id=str(uuid.uuid4()),
            driver=self.name,
            workdir=__import__("pathlib").Path("/tmp"),
            mission_id="fake",
            session_id=session_id,
        )
        self.provisioned.append(h)
        return h

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
        self.destroyed.append(handle.id)

    async def spawn_lsp(self, handle, language):  # type: ignore[override]
        # P1-3 — pool tests don't exercise the LSP surface but the abstract
        # base requires the method; surface a typed error so a caller that
        # somehow lands here gets the same wire-shape as production.
        from app.sandbox.lsp import LSPUnavailableError

        raise LSPUnavailableError("binary_not_found", language)


@pytest.mark.asyncio
async def test_pool_respects_max_concurrent() -> None:
    from app.config import Settings

    s = Settings(sandbox_max_concurrent=2)
    pool = SandboxPool(settings=s, driver=_FakeDriver())

    h1 = await pool.acquire(None, uuid.uuid4())
    h2 = await pool.acquire(None, uuid.uuid4())

    acquire_task = asyncio.create_task(pool.acquire(None, uuid.uuid4()))
    await asyncio.sleep(0.05)
    assert not acquire_task.done(), "third acquire should block past the cap"

    await pool.release(h1)
    h3 = await asyncio.wait_for(acquire_task, timeout=1.0)
    assert h3 is not None

    await pool.release(h2)
    await pool.release(h3)


@pytest.mark.asyncio
async def test_pool_reaper_kills_expired_handles() -> None:
    from app.config import Settings

    s = Settings(sandbox_timeout_seconds=1)
    pool = SandboxPool(settings=s, driver=_FakeDriver())

    h = await pool.acquire(None, uuid.uuid4())
    # Backdate both clocks so the reaper considers it expired. The new pool
    # checks idle time against ``last_activity_at`` rather than ``created_at``;
    # legacy tests set both for forward-compat.
    h.created_at = datetime.now(UTC) - timedelta(seconds=5)
    h.driver_state["last_activity_at"] = datetime.now(UTC) - timedelta(seconds=5)

    await pool._reap_once()

    assert pool.get(h.id) is None
