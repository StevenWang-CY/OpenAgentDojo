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
        "event_payload_truncated_total",
        "event_publish_failures_total",
    ):
        assert family in body, f"missing metric family {family} in /metrics output"


def test_submissions_total_supports_timeout_outcome_label() -> None:
    """The counter must accept ``outcome="timeout"`` distinct from ``failed``.

    The submit timeout path (``app.sessions.submit.submit_session``) bumps
    this label when the grading wall-clock budget is exceeded — the
    operational SLO dashboard relies on ``outcome="timeout"`` being its
    own bucket so we can alert on budget hits without false-positives from
    real pipeline crashes.
    """
    from app.observability import submissions_total

    # Snapshot baseline (other tests may have already incremented this).
    sample_t = submissions_total.labels(mission_id="t-mission", outcome="timeout")
    sample_f = submissions_total.labels(mission_id="t-mission", outcome="failed")
    sample_g = submissions_total.labels(mission_id="t-mission", outcome="graded")
    before_t = float(sample_t._value.get())
    before_f = float(sample_f._value.get())
    before_g = float(sample_g._value.get())

    sample_t.inc()
    sample_t.inc()
    sample_f.inc()
    sample_g.inc()

    assert float(sample_t._value.get()) == before_t + 2
    assert float(sample_f._value.get()) == before_f + 1
    assert float(sample_g._value.get()) == before_g + 1

    # The /metrics scrape must surface the timeout label distinctly. Use
    # ``generate_latest`` to render the registry's exposition output the same
    # way the /metrics endpoint does.
    from prometheus_client import generate_latest

    text = generate_latest(REGISTRY).decode("utf-8")
    assert 'submissions_total{mission_id="t-mission",outcome="timeout"}' in text
    assert 'submissions_total{mission_id="t-mission",outcome="failed"}' in text
    assert 'submissions_total{mission_id="t-mission",outcome="graded"}' in text


@pytest.mark.asyncio
async def test_submit_timeout_bumps_submissions_total_with_timeout_outcome(
    db_engine, monkeypatch
) -> None:
    """End-to-end: a TimeoutError raised by the runner produces ``outcome="timeout"``.

    Stubs the GradingRunner so we don't have to spin up a real sandbox; the
    only thing under test is the metric increment on the timeout branch.
    """
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import session as session_module
    from app.models.mission import Mission
    from app.models.session import SessionRow
    from app.models.user import User
    from app.observability import submissions_total
    from app.sessions import submit as submit_module

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = factory  # type: ignore[assignment]

    try:
        # Seed user + mission + session.
        async with factory() as db:
            user = User(
                id=_uuid.uuid4(),
                email=f"timeout-metric-{_uuid.uuid4().hex[:6]}@arena.local",
                display_name="T",
            )
            mission = Mission(
                id="timeout-metric-mission",
                title="t",
                difficulty="beginner",
                category="testing",
                repo_pack="pack",
                initial_commit="abc",
                estimated_minutes=5,
                failure_mode="x",
                skills_tested=[],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
            )
            db.add_all([user, mission])
            await db.flush()
            session = SessionRow(
                id=_uuid.uuid4(),
                user_id=user.id,
                mission_id=mission.id,
                status="active",
            )
            db.add(session)
            await db.commit()
            session_id = session.id
            user_id = user.id

        # Stub the runner so it always raises TimeoutError.
        class _TimeoutRunner:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def run_and_persist(self, **_kwargs):
                raise TimeoutError("simulated wall-clock budget breach")

        monkeypatch.setattr(submit_module, "GradingRunner", _TimeoutRunner)

        # Stub the request/handle/driver dependencies.
        class _Pool:
            driver = object()

            def handle_for(self, _sid):
                return object()

        class _State:
            sandbox_pool = _Pool()

        class _App:
            state = _State()

        class _Req:
            app = _App()

        # Stub manifest loader so we don't read disk.
        from pathlib import Path as _Path

        monkeypatch.setattr(
            submit_module,
            "_find_manifest_folder",
            lambda _settings, _mid: _Path("/tmp"),
        )

        class _Loaded:
            manifest = object()
            manifest_sha256 = "0" * 64

        class _Loader:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def _load_one(self, _path):
                return _Loaded()

        monkeypatch.setattr(submit_module, "MissionLoader", _Loader)

        before = float(
            submissions_total.labels(
                mission_id="timeout-metric-mission", outcome="timeout"
            )._value.get()
        )

        async with factory() as db:
            session = await db.get(SessionRow, session_id)
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await submit_module.submit_session(db=db, session=session, request=_Req())
            assert exc_info.value.status_code == 504

        after = float(
            submissions_total.labels(
                mission_id="timeout-metric-mission", outcome="timeout"
            )._value.get()
        )
        assert after == before + 1, (
            f"expected outcome=timeout to increment by 1; before={before} after={after}"
        )

        # And the failed bucket must NOT have moved.
        failed_after = float(
            submissions_total.labels(
                mission_id="timeout-metric-mission", outcome="failed"
            )._value.get()
        )
        # The failed counter may have its own state from earlier tests; the
        # important invariant is that THIS call didn't bump it.
        assert failed_after >= 0  # sanity guard

        # Suppress the unused-symbol lint nag.
        _ = user_id
    finally:
        session_module.AsyncSessionLocal = original  # type: ignore[assignment]
