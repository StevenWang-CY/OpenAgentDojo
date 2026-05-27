"""Idle reaper respects activity timestamps and submitting/graded sessions.

* Handles whose ``last_activity_at`` is older than ``sandbox_timeout_seconds``
  are reaped.
* Handles whose backing DB session is in ``graded`` / ``submitting`` are
  skipped even if they look idle (so the grading pipeline can finish).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


class _RecordingDriver(SandboxDriver):
    name = "recording"

    def __init__(self) -> None:
        self.destroyed: list[str] = []

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
        self.destroyed.append(handle.id)

    async def spawn_lsp(self, handle, language):  # type: ignore[override]
        # P1-3 — reaper tests don't drive the LSP surface; raise the typed
        # unavailable error so the abstract contract is satisfied without
        # pulling a real subprocess into the test path.
        from app.sandbox.lsp import LSPUnavailableError

        raise LSPUnavailableError("binary_not_found", language)


@pytest.mark.asyncio
async def test_reaper_kills_handle_whose_last_activity_is_stale(monkeypatch) -> None:
    from app.config import Settings

    driver = _RecordingDriver()
    pool = SandboxPool(settings=Settings(sandbox_timeout_seconds=1), driver=driver)

    # Skip DB status lookup — pretend nothing is graded.
    async def _no_graded(_self, _ids, _statuses):
        return set()

    monkeypatch.setattr(SandboxPool, "_sessions_with_status", _no_graded)

    h = await pool.acquire(None, uuid.uuid4())
    # Backdate the activity stamp so it appears idle past the TTL.
    h.driver_state["last_activity_at"] = datetime.now(UTC) - timedelta(seconds=30)

    await pool._reap_once()
    assert pool.get(h.id) is None
    assert h.id in driver.destroyed


@pytest.mark.asyncio
async def test_reaper_recent_activity_blocks_reap(monkeypatch) -> None:
    from app.config import Settings

    driver = _RecordingDriver()
    pool = SandboxPool(settings=Settings(sandbox_timeout_seconds=60), driver=driver)

    async def _no_graded(_self, _ids, _statuses):
        return set()

    monkeypatch.setattr(SandboxPool, "_sessions_with_status", _no_graded)

    h = await pool.acquire(None, uuid.uuid4())
    # Fresh activity — even if created_at were old, activity wins.
    h.created_at = datetime.now(UTC) - timedelta(hours=2)
    h.driver_state["last_activity_at"] = datetime.now(UTC)

    await pool._reap_once()
    assert pool.get(h.id) is not None
    assert h.id not in driver.destroyed

    await pool.release(h)


@pytest.mark.asyncio
async def test_reaper_skips_graded_or_submitting_sessions(monkeypatch) -> None:
    from app.config import Settings

    driver = _RecordingDriver()
    pool = SandboxPool(settings=Settings(sandbox_timeout_seconds=1), driver=driver)

    # Pretend every active session is currently 'graded' so the reaper has to
    # leave them alone.
    graded_sids: set[uuid.UUID] = set()

    async def _all_graded(_self, ids, _statuses):
        return set(ids)

    monkeypatch.setattr(SandboxPool, "_sessions_with_status", _all_graded)

    h = await pool.acquire(None, uuid.uuid4())
    graded_sids.add(h.session_id)
    h.driver_state["last_activity_at"] = datetime.now(UTC) - timedelta(hours=1)

    await pool._reap_once()
    assert pool.get(h.id) is not None
    assert h.id not in driver.destroyed

    await pool.release(h)
