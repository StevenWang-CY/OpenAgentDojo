"""Pool destroy() failure must dead-letter the container + decrement the gauge.

When ``SandboxDriver.destroy`` raises (e.g. the docker daemon hiccups for a
moment), the pool used to leak the container forever and double-account the
``sessions_active`` gauge — every subsequent release call would dec the gauge
into negative territory because we never recovered from the raise.

Two invariants we pin here:

* The handle's ``container_id`` lands in the module-level dead-letter store
  so the orphan sweeper can retry ``docker rm -f`` later.
* ``sessions_active`` is decremented exactly once even when destroy raises —
  the gauge mutation is wrapped in a ``finally``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

import app.sandbox.pool as pool_module
from app.config import Settings
from app.observability import sessions_active
from app.sandbox.driver import SandboxDriver
from app.sandbox.pool import SandboxPool, dead_letter_handles
from app.sandbox.types import ApplyResult, FileTreeNode, GradingArtifacts, RunResult, SandboxHandle


class _DestroyRaisingDriver(SandboxDriver):
    """Driver whose ``destroy()`` always raises — simulates a docker outage."""

    name = "destroy-raises"

    def __init__(self) -> None:
        self.destroy_called: int = 0

    async def provision(self, mission: Any, session_id: uuid.UUID) -> SandboxHandle:
        # Stash a synthetic container id in driver_state so the dead-letter
        # entry has something to record (mirroring the docker driver shape).
        h = SandboxHandle(
            id=str(uuid.uuid4()),
            driver=self.name,
            workdir=Path("/tmp"),
            mission_id="dl-test",
            session_id=session_id,
            driver_state={"container_id": f"ctr-{uuid.uuid4().hex[:8]}"},
        )
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
        self.destroy_called += 1
        raise RuntimeError("docker daemon disappeared")


def _gauge_value() -> float:
    return float(sessions_active._value.get())


@pytest.mark.asyncio
async def test_release_dead_letters_on_destroy_failure(monkeypatch) -> None:
    # Reset the module-level dead-letter so we can assert membership exactly.
    pool_module._DEAD_LETTER.clear()

    driver = _DestroyRaisingDriver()
    pool = SandboxPool(settings=Settings(sandbox_max_concurrent=2), driver=driver)

    before_gauge = _gauge_value()
    handle = await pool.acquire(None, uuid.uuid4())
    container_id = handle.driver_state["container_id"]
    # acquire() incremented the gauge by exactly 1.
    assert _gauge_value() == pytest.approx(before_gauge + 1)

    # Release must not raise even though destroy did; the handle should be
    # gone from the pool and recorded in the dead-letter store.
    await pool.release(handle)

    # Gauge has returned to baseline — the ``finally`` block fired exactly once.
    assert _gauge_value() == pytest.approx(before_gauge)
    assert pool.get(handle.id) is None
    assert driver.destroy_called == 1

    dl = dead_letter_handles()
    assert container_id in dl, dl
    assert dl[container_id] == handle.id


@pytest.mark.asyncio
async def test_dead_letter_handles_returns_snapshot() -> None:
    """``dead_letter_handles()`` returns a copy — mutating the result is safe."""
    pool_module._DEAD_LETTER.clear()
    pool_module._DEAD_LETTER["ctr-snap"] = "h-snap"

    snap = dead_letter_handles()
    snap["mutate"] = "noop"

    # The module-level store was NOT modified by our local mutation.
    assert "mutate" not in pool_module._DEAD_LETTER
    assert pool_module._DEAD_LETTER["ctr-snap"] == "h-snap"
    pool_module._DEAD_LETTER.clear()
