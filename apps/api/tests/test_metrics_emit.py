"""Provisioning a sandbox emits the documented Prometheus metrics.

We acquire + release a handle through ``SandboxPool`` with a fake driver and
assert that ``/metrics`` reports:

* ``sessions_active`` ≥ 1 during the acquire
* ``sessions_active`` decremented after release
* ``sessions_provision_seconds_count`` incremented

The scrape goes through the real ASGI metrics app so we exercise both the
registry wiring and the Prometheus exposition format.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.observability import REGISTRY, metrics_asgi_app
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

    async def provision(self, mission, session_id):  # type: ignore[override]
        # Sleep briefly so sessions_provision_seconds has a non-zero observation.
        await asyncio.sleep(0.01)
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


def _gauge_value(name: str) -> float:
    """Read the current value of a gauge from the shared registry."""
    for metric in REGISTRY.collect():
        if metric.name == name:
            for sample in metric.samples:
                if sample.name == name:
                    return sample.value
    return 0.0


def _hist_count(name: str) -> float:
    """Sum of all bucket observations across labels for a histogram."""
    total = 0.0
    target = f"{name}_count"
    for metric in REGISTRY.collect():
        if metric.name != name:
            continue
        for sample in metric.samples:
            if sample.name == target:
                total += sample.value
    return total


@pytest.mark.asyncio
async def test_pool_increments_sessions_active_and_provision_histogram() -> None:
    from app.config import Settings

    pool = SandboxPool(settings=Settings(sandbox_max_concurrent=2), driver=_FakeDriver())

    active_before = _gauge_value("sessions_active")
    provision_before = _hist_count("sessions_provision_seconds")

    handle = await pool.acquire(None, uuid.uuid4())

    assert _gauge_value("sessions_active") == active_before + 1
    assert _hist_count("sessions_provision_seconds") == provision_before + 1

    await pool.release(handle)
    assert _gauge_value("sessions_active") == active_before


@pytest.mark.asyncio
async def test_metrics_endpoint_serves_prometheus_text() -> None:
    """The /metrics ASGI app round-trips and includes our counter families."""
    transport = ASGITransport(app=metrics_asgi_app())
    async with AsyncClient(transport=transport, base_url="http://metrics") as ac:
        resp = await ac.get("/")
    assert resp.status_code == 200
    body = resp.text
    # All metric families we ship must appear at least once in HELP/TYPE lines.
    for family in (
        "sessions_active",
        "sessions_provision_seconds",
        "submissions_total",
        "agent_responses_total",
        "agent_llm_fallback_total",
        "llm_calls_total",
        "llm_latency_seconds",
    ):
        assert family in body, f"missing metric family {family} in /metrics output"
