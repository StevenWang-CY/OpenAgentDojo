"""P2 — ``submission.requested`` must be anchored to the real submit boundary.

The rushed-submit signal (``app.grading.score._score_agent_review``) and the
``missed_corrective_window`` diagnostic both diff
``submission.requested.occurred_at`` against ``agent.responded.occurred_at``
over a 15s window. Historically the ``submission.requested`` event was stamped
INSIDE the runner with ``datetime.now(UTC)`` at grading START — i.e. AFTER the
submit-route claim, the handle lookup, the manifest load, and runner entry.
Under slow sandbox provisioning that gap inflates the agent→submit delta, so a
genuine sub-15s rushed submit can flip to "not rushed" on the FIRST grade and
the agent_review hard-zero is silently suppressed.

The fix threads the route-boundary submit timestamp through
``GradingRunner.run_and_persist`` so the persisted
``submission.requested.occurred_at`` reflects when the user actually pressed
submit. This test seeds an ``agent.responded`` event that is sub-15s before the
submit boundary but MORE than 15s before grading-start (the real wall-clock
``now()`` at the time ``run_and_persist`` runs), and asserts:

  * the persisted ``submission.requested`` row carries the submit-boundary
    timestamp, NOT the grading-start ``now()``; and
  * the rushed-submit hard-zero fires (agent_review == 0/15).

Both assertions FAIL before the fix (the event is stamped at grading start,
which is >15s after ``agent.responded`` → not rushed) and PASS after it.

Reuses the canned-driver harness shape from ``test_runner_resilience`` to keep
the scoring path identical to the existing runner tests.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.grading.runner import GradingRunner
from app.missions.loader import MissionLoader
from app.models.agent_turn import AgentTurn
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.sandbox.types import GradingArtifacts

_DIFF_FENCE_RE = re.compile(r"```diff\n(.*?)\n```", re.DOTALL)


def _ideal_diff_text(folder: Path) -> str:
    md = (folder / "ideal_solution.md").read_text(encoding="utf-8")
    blocks = _DIFF_FENCE_RE.findall(md)
    assert blocks, "ideal_solution.md has no ```diff``` block"
    return "\n".join(blocks)


def _load_mission01_manifest():
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    folder = settings.missions_root / "01-auth-cookie-expiration"
    loader = MissionLoader(settings.missions_root)
    loaded = loader._load_one(folder / "mission.yaml")
    return loaded.manifest, folder


class _CannedDriver:
    def __init__(self, diff: str) -> None:
        self._diff = diff

    async def freeze_and_grade(self, handle, mission, *, manifest_folder=None):
        return GradingArtifacts(
            diff=self._diff,
            test_results={
                "unit": {
                    "suite": "unit",
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": 5,
                    "failed": 0,
                    "skipped": 0,
                    "timed_out": False,
                },
                "hidden": {
                    "suite": "hidden",
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": 4,
                    "failed": 0,
                    "skipped": 0,
                    "timed_out": False,
                },
            },
            logs={},
        )

    async def read_file(self, handle, path):
        return b""


class _FakeHandle:
    def __init__(self) -> None:
        self.workdir = Path("/nonexistent-workdir-for-test")


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_mission_and_session(session_factory, mission_id: str) -> uuid.UUID:
    async with session_factory() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"submit-ts-{uuid.uuid4().hex[:8]}@arena.local",
            display_name="SubmitTs",
        )
        db.add(user)
        mission = Mission(
            id=mission_id,
            title=mission_id,
            difficulty="intermediate",
            category="auth",
            repo_pack="fullstack-auth-demo",
            initial_commit="HEAD",
            estimated_minutes=10,
            failure_mode="x",
            skills_tested=["auth"],
            manifest_sha256="sha",
            version=1,
            published=True,
            expected_weak_dim="agent_review",
        )
        db.add(mission)
        await db.flush()
        session = SessionRow(user_id=user.id, mission_id=mission.id, status="active")
        db.add(session)
        await db.commit()
        return session.id


async def _seed_rushed_events(
    db,
    session_id: uuid.UUID,
    *,
    agent_responded_at: datetime,
) -> None:
    """Seed an agent.responded + patch but deliberately NO diff.opened.

    The absence of ``diff.opened`` is required for the rushed-submit hard-zero
    branch to engage; the timestamps are chosen so the agent responded sub-15s
    before the submit boundary but >15s before grading-start.
    """
    rows = [
        SupervisionEvent(
            session_id=session_id,
            event_type="prompt.submitted",
            payload={"turn_index": 0, "text": "fix the cookie expiration bug", "intent": "fix"},
            occurred_at=agent_responded_at - timedelta(seconds=5),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="patch.applied",
            payload={"turn_index": 0, "files_changed": 1, "added": 6, "removed": 1},
            occurred_at=agent_responded_at - timedelta(seconds=2),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="agent.responded",
            payload={"turn_index": 0},
            occurred_at=agent_responded_at,
        ),
    ]
    for r in rows:
        db.add(r)
    db.add(
        AgentTurn(
            session_id=session_id,
            turn_index=0,
            user_prompt="fix the cookie expiration bug",
            selected_context={"files": []},
            agent_response="done",
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_submission_requested_anchored_to_submit_boundary(session_factory) -> None:
    """The persisted ``submission.requested`` carries the submit boundary ts.

    Without the fix the event is stamped at grading-start ``now()`` which is
    >15s after ``agent.responded``; the rushed-submit window misses and
    agent_review keeps its non-zero score. With the fix the event reflects the
    sub-15s submit boundary and the hard-zero fires.
    """
    manifest, folder = _load_mission01_manifest()
    diff = _ideal_diff_text(folder)
    driver = _CannedDriver(diff=diff)
    handle = _FakeHandle()

    session_id = await _seed_mission_and_session(session_factory, manifest.id)

    # Anchor everything relative to the wall clock the runner will see when it
    # stamps the event today. Grading-start is ~``now()``; we place
    # ``agent.responded`` 25s before now (so the LEGACY behaviour computes a
    # 25s delta → not rushed) but only 3s before the submit boundary (so the
    # FIXED behaviour computes a 3s delta → rushed).
    grading_start = datetime.now(UTC)
    agent_responded_at = grading_start - timedelta(seconds=25)
    submitted_at = agent_responded_at + timedelta(seconds=3)  # sub-15s rushed submit

    async with session_factory() as db:
        await _seed_rushed_events(db, session_id, agent_responded_at=agent_responded_at)
        await db.commit()

    async with session_factory() as db:
        session = await db.get(SessionRow, session_id)
        runner = GradingRunner(settings=None, budget_seconds=60)
        submission, _result = await runner.run_and_persist(
            db=db,
            session=session,
            driver=driver,
            handle=handle,
            manifest=manifest,
            manifest_folder=folder,
            manifest_sha256="deadbeef" * 8,
            submitted_at=submitted_at,
        )

    # 1) The persisted submission.requested row reflects the submit boundary,
    #    rounded to seconds (the resolution other event rows carry), NOT the
    #    grading-start now().
    async with session_factory() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session_id,
                    SupervisionEvent.event_type == "submission.requested",
                )
            )
        ).scalar_one()
    expected = submitted_at.astimezone(UTC).replace(microsecond=0)
    persisted = row.occurred_at
    if persisted.tzinfo is None:
        persisted = persisted.replace(tzinfo=UTC)
    assert persisted == expected, (
        f"submission.requested.occurred_at={persisted} should equal the submit "
        f"boundary {expected}, not grading-start ~{grading_start}"
    )
    # Guard: prove it is NOT the grading-start instant (the legacy behaviour).
    assert abs((persisted - grading_start).total_seconds()) >= 15

    # 2) The rushed-submit hard-zero fires because the REAL submit→agent delta
    #    is sub-15s. Before the fix this dimension would carry a non-zero
    #    score (the 25s grading-start delta suppressed the rushed branch).
    agent_review = submission.score_report["dimensions"]["agent_review"]
    assert agent_review["score"] == 0, (
        "rushed-submit hard-zero should fire for a sub-15s submit boundary; "
        f"got agent_review={agent_review}"
    )
