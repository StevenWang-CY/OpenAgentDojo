"""GradingRunner end-to-end tests.

Covers:
  - Happy path: a Mission 01 ideal-solution diff + seeded supervision events
    scores >= 92 (using a fake driver to simulate green hidden tests).
  - Agent-patch path: the unmodified agent diff + minimal events scores 35..60.
  - Error path: a misbehaving validator returns fail-soft, the run completes.
  - Timeout path: tight budget triggers asyncio.TimeoutError + status=error.

The "real toolchain" mission run lives in ``scripts/check_missions.py`` — it
requires pnpm + node deps and is gated separately.
"""

from __future__ import annotations

import asyncio
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


_IDEAL_REGRESSION_TEST_PATCH = """\
--- a/backend/src/tests/integration/auth.test.ts
+++ b/backend/src/tests/integration/auth.test.ts
@@ -1,1 +1,5 @@
 const placeholder = 1;
+it("redirects when the session cookie is expired", async () => {
+  // exercises Session.isValid() ttl/expiration path.
+  expect(true).toBe(true);
+});
"""


def _ideal_diff_text(folder: Path) -> str:
    md = (folder / "ideal_solution.md").read_text(encoding="utf-8")
    blocks = _DIFF_FENCE_RE.findall(md)
    assert blocks, "ideal_solution.md has no ```diff``` block"
    # The markdown's regression test sample is in a ```ts``` fence (not diff),
    # so the parser misses it. A strong supervisor would include a real test
    # file in their diff — append a synthetic test patch so the
    # regression_test_required validator sees it.
    return "\n".join(blocks) + "\n" + _IDEAL_REGRESSION_TEST_PATCH


def _agent_diff_text(folder: Path) -> str:
    return (folder / "agent_patch.diff").read_text(encoding="utf-8")


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_mission_and_session(session_factory, mission_id: str) -> uuid.UUID:
    async with session_factory() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"grader-{mission_id}@arena.local",
            display_name="Grader",
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
            expected_weak_dim="safety",
        )
        db.add(mission)
        await db.flush()
        session = SessionRow(user_id=user.id, mission_id=mission.id, status="active")
        db.add(session)
        await db.commit()
        return session.id


async def _seed_supervision_events(
    db,
    session_id: uuid.UUID,
    manifest_required: list[str],
    manifest_recommended: list[str],
) -> None:
    """Seed the events that drive process-based scoring dimensions."""
    base = datetime.now(UTC) - timedelta(minutes=10)

    def at(off_s: int) -> datetime:
        return base + timedelta(seconds=off_s)

    rows = [
        # Right context selected up front.
        SupervisionEvent(
            session_id=session_id,
            event_type="context.selected",
            payload={
                "files": manifest_required + manifest_recommended,
                "logs": [],
                "tests": [],
                "extras": [],
            },
            occurred_at=at(0),
        ),
        # Strong prompt: long, mentions regression test + expiration + minimal.
        SupervisionEvent(
            session_id=session_id,
            event_type="prompt.submitted",
            payload={
                "turn_index": 0,
                "text": (
                    "Reproduce the bug: an expired cookie still gets past "
                    "requireAuth. Add a regression test that exercises expiration. "
                    "Keep the patch minimal — do not modify the frontend."
                ),
                "char_count": 220,
                "keyword_hits": ["regression test", "expiration", "minimal"],
                "intent": "fix",
            },
            occurred_at=at(60),
        ),
        # Agent responds, patch applied.
        SupervisionEvent(
            session_id=session_id,
            event_type="agent.responded",
            payload={"turn_index": 0, "response_summary": "applied patch"},
            occurred_at=at(70),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="patch.applied",
            payload={"turn_index": 0, "files_changed": 2, "added": 18, "removed": 4},
            occurred_at=at(80),
        ),
        # User reviews the diff and edits a file (corrects the agent).
        SupervisionEvent(
            session_id=session_id,
            event_type="diff.opened",
            payload={"path": "backend/src/middleware/requireAuth.ts"},
            occurred_at=at(90),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="file.edited",
            payload={
                "path": "backend/src/middleware/requireAuth.ts",
                "added": 4,
                "removed": 1,
                "source": "user",
            },
            occurred_at=at(100),
        ),
        # Corrective revise prompt.
        SupervisionEvent(
            session_id=session_id,
            event_type="prompt.submitted",
            payload={
                "turn_index": 1,
                "text": "Revise — add a test for the refresh path.",
                "intent": "revise",
            },
            occurred_at=at(110),
        ),
        # Verification: targeted auth test + typecheck + lint all run.
        SupervisionEvent(
            session_id=session_id,
            event_type="command.run",
            payload={
                "command": "pnpm test:unit -- auth",
                "category": "test",
                "exit_code": 0,
                "duration_ms": 1200,
            },
            occurred_at=at(150),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="command.run",
            payload={
                "command": "pnpm typecheck",
                "category": "typecheck",
                "exit_code": 0,
                "duration_ms": 800,
            },
            occurred_at=at(160),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="command.run",
            payload={
                "command": "pnpm lint",
                "category": "lint",
                "exit_code": 0,
                "duration_ms": 400,
            },
            occurred_at=at(170),
        ),
    ]
    for r in rows:
        db.add(r)
    db.add(
        AgentTurn(
            session_id=session_id,
            turn_index=0,
            user_prompt=(
                "Reproduce the bug: an expired cookie still gets past "
                "requireAuth. Add a regression test that exercises expiration. "
                "Keep the patch minimal — do not modify the frontend."
            ),
            selected_context={"files": manifest_required},
            agent_response="seed",
        )
    )
    await db.flush()


def _load_mission01_manifest():
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    folder = settings.missions_root / "01-auth-cookie-expiration"
    loader = MissionLoader(settings.missions_root)
    loaded = loader._load_one(folder / "mission.yaml")
    return loaded.manifest, folder


# ---------------------------------------------------------------------------
# Fake sandbox driver — produces canned grading artifacts so the runner can
# focus on scoring + validator + badge orchestration.
# ---------------------------------------------------------------------------


class _CannedDriver:
    """Driver that returns pre-built GradingArtifacts."""

    def __init__(
        self,
        diff: str,
        visible_pass: bool,
        hidden_pass: bool,
        *,
        sleep_for: float = 0.0,
    ) -> None:
        self._diff = diff
        self._visible_pass = visible_pass
        self._hidden_pass = hidden_pass
        self._sleep_for = sleep_for

    async def freeze_and_grade(self, handle, mission, *, manifest_folder=None):
        if self._sleep_for:
            await asyncio.sleep(self._sleep_for)
        return GradingArtifacts(
            diff=self._diff,
            test_results={
                "unit": {
                    "suite": "unit",
                    "exit_code": 0 if self._visible_pass else 1,
                    "stdout": "",
                    "stderr": "",
                    "passed": 5 if self._visible_pass else 0,
                    "failed": 0 if self._visible_pass else 5,
                    "skipped": 0,
                    "timed_out": False,
                },
                "typecheck": {
                    "suite": "typecheck",
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "timed_out": False,
                },
                "hidden": {
                    "suite": "hidden",
                    "exit_code": 0 if self._hidden_pass else 1,
                    "stdout": "",
                    "stderr": "",
                    "passed": 4 if self._hidden_pass else 0,
                    "failed": 0 if self._hidden_pass else 4,
                    "skipped": 0,
                    "timed_out": False,
                },
            },
            logs={},
        )

    async def read_file(self, handle, path):
        # The forbidden_changes rules for Mission 01 reference
        # backend/src/middleware/requireAuth.ts and look for the
        # requireAuth identifier — provide it.
        if "requireAuth" in path:
            return b"export function requireAuth(req, res, next) { next(); }"
        return b""


class _FakeHandle:
    def __init__(self) -> None:
        self.workdir = Path("/nonexistent-workdir-for-test")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_ideal_solution_scores_at_least_92(
    session_factory,
) -> None:
    manifest, folder = _load_mission01_manifest()
    diff = _ideal_diff_text(folder)
    driver = _CannedDriver(diff=diff, visible_pass=True, hidden_pass=True)
    handle = _FakeHandle()

    session_id = await _seed_mission_and_session(session_factory, manifest.id)

    async with session_factory() as db:
        await _seed_supervision_events(
            db,
            session_id,
            manifest_required=list(manifest.expected_context.required),
            manifest_recommended=list(manifest.expected_context.recommended),
        )
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
        )

    assert submission.total_score >= 92, (
        f"ideal scored {submission.total_score}, expected >= 92; "
        f"breakdown={submission.score_report.get('dimensions')}"
    )
    # Status was flipped to graded.
    async with session_factory() as db:
        refreshed = await db.get(SessionRow, session_id)
        assert refreshed.status == "graded"
        assert refreshed.score == submission.total_score


@pytest.mark.asyncio
async def test_runner_agent_patch_lands_in_unmodified_band(
    session_factory,
) -> None:
    manifest, folder = _load_mission01_manifest()
    diff = _agent_diff_text(folder)
    # Agent patch passes visible but fails hidden.
    driver = _CannedDriver(diff=diff, visible_pass=True, hidden_pass=False)
    handle = _FakeHandle()

    session_id = await _seed_mission_and_session(session_factory, manifest.id)

    # Minimal supervision events: only context selected (no diff opened, no
    # corrective prompt, no targeted test) — simulating an inattentive user
    # who accepts the patch verbatim.
    async with session_factory() as db:
        db.add(
            SupervisionEvent(
                session_id=session_id,
                event_type="context.selected",
                payload={
                    "files": list(manifest.expected_context.required),
                    "logs": [],
                    "tests": [],
                    "extras": [],
                },
            )
        )
        db.add(
            SupervisionEvent(
                session_id=session_id,
                event_type="patch.applied",
                payload={"turn_index": 0, "files_changed": 1},
            )
        )
        db.add(
            SupervisionEvent(
                session_id=session_id,
                event_type="command.run",
                payload={
                    "command": "pnpm test:unit",
                    "category": "test",
                    "exit_code": 0,
                    "duration_ms": 800,
                },
            )
        )
        db.add(
            AgentTurn(
                session_id=session_id,
                turn_index=0,
                user_prompt="please fix the bug",
                selected_context={"files": []},
                agent_response="ok",
            )
        )
        await db.commit()

    async with session_factory() as db:
        session = await db.get(SessionRow, session_id)
        runner = GradingRunner(settings=None, budget_seconds=60)
        submission, _ = await runner.run_and_persist(
            db=db,
            session=session,
            driver=driver,
            handle=handle,
            manifest=manifest,
            manifest_folder=folder,
            manifest_sha256="deadbeef" * 8,
        )

    assert 35 <= submission.total_score <= 60, (
        f"agent-patch scored {submission.total_score}, expected 35..60; "
        f"breakdown={submission.score_report.get('dimensions')}"
    )


@pytest.mark.asyncio
async def test_runner_validator_exception_is_fail_soft(monkeypatch, session_factory) -> None:
    """If a validator raises, the runner records a failure and continues."""
    from app.grading import runner as runner_mod

    def _explode(rule, ctx):
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(runner_mod, "dispatch", _explode)

    manifest, folder = _load_mission01_manifest()
    driver = _CannedDriver(diff="", visible_pass=True, hidden_pass=False)
    handle = _FakeHandle()
    session_id = await _seed_mission_and_session(session_factory, manifest.id)

    async with session_factory() as db:
        session = await db.get(SessionRow, session_id)
        runner = GradingRunner(settings=None, budget_seconds=30)
        submission, result = await runner.run_and_persist(
            db=db,
            session=session,
            driver=driver,
            handle=handle,
            manifest=manifest,
            manifest_folder=folder,
            manifest_sha256="deadbeef" * 8,
        )

    # Each broken validator surfaces a passed=False ValidatorResult. The
    # runner now emits validator_results as a list of dicts (one per
    # validator) so the FE narrowed type stays honest; switch over.
    validator_iter = (
        result.validator_results
        if isinstance(result.validator_results, list)
        else [{"kind": k, **v} for k, v in result.validator_results.items()]
    )
    for vres in validator_iter:
        if vres.get("kind") == "tests_pass":
            continue
        assert vres["passed"] is False
        assert any("validator error" in v for v in vres["violations"])
    # Session still ended in 'graded' status.
    async with session_factory() as db:
        refreshed = await db.get(SessionRow, session_id)
        assert refreshed.status == "graded"


@pytest.mark.asyncio
async def test_runner_timeout_marks_session_error(session_factory) -> None:
    """A tight wall-clock budget makes the runner fail with status='error'."""
    manifest, folder = _load_mission01_manifest()
    driver = _CannedDriver(diff="", visible_pass=True, hidden_pass=True, sleep_for=0.5)
    handle = _FakeHandle()
    session_id = await _seed_mission_and_session(session_factory, manifest.id)

    async with session_factory() as db:
        session = await db.get(SessionRow, session_id)
        runner = GradingRunner(settings=None, budget_seconds=0.1)
        with pytest.raises(asyncio.TimeoutError):
            await runner.run(
                db=db,
                session=session,
                driver=driver,
                handle=handle,
                manifest=manifest,
                manifest_folder=folder,
                manifest_sha256="deadbeef" * 8,
            )

    async with session_factory() as db:
        refreshed = await db.get(SessionRow, session_id)
        assert refreshed.status == "error"
        rows = (
            (
                await db.execute(
                    select(SupervisionEvent).where(
                        SupervisionEvent.session_id == session_id,
                        SupervisionEvent.event_type == "submission.failed",
                    )
                )
            )
            .scalars()
            .all()
        )
        # New contract (P0-B1): payload uses `stage` + `detail` not `reason`.
        assert any(
            (e.payload or {}).get("stage") == "grading"
            and "budget" in (e.payload or {}).get("detail", "")
            for e in rows
        )


@pytest.mark.asyncio
async def test_submission_graded_breakdown_always_has_seven_dimensions(
    monkeypatch, session_factory, caplog
) -> None:
    """A partial score_report MUST still surface all 7 axes in the event payload.

    The FE radar chart silently drops missing keys, which looks like a scoring
    bug to the user even when the pipeline was healthy. P1-B4 makes the
    runner backfill any dimension compute_score didn't return with a zero
    score + ``dimension_missing`` signal — and log a warning so an actual
    upstream regression surfaces in the operator's logs.
    """
    from app.grading import runner as runner_mod
    from app.grading.score import DimensionScore, ScoreReport

    manifest, folder = _load_mission01_manifest()
    diff = _agent_diff_text(folder)
    driver = _CannedDriver(diff=diff, visible_pass=True, hidden_pass=True)
    handle = _FakeHandle()
    session_id = await _seed_mission_and_session(session_factory, manifest.id)

    # Stub compute_score to return a report missing the `safety` dimension
    # (and `prompt_quality`, to prove multi-key defaulting works).
    def _stub_compute_score(
        *,
        diff,
        events,
        validator_results,
        test_results,
        manifest,
        agent_turns,
        prompt_judgements=None,
        completed_mission_ids=None,
        engine_recommended_mission_ids=None,
    ):
        from app.grading.dimensions import DIMENSION_MAX

        partial: dict[str, DimensionScore] = {
            "final_correctness": DimensionScore(
                score=20, max_score=DIMENSION_MAX["final_correctness"], signals=["stub"]
            ),
            "verification": DimensionScore(
                score=12, max_score=DIMENSION_MAX["verification"], signals=["stub"]
            ),
            "agent_review": DimensionScore(
                score=10, max_score=DIMENSION_MAX["agent_review"], signals=["stub"]
            ),
            "context_selection": DimensionScore(
                score=6, max_score=DIMENSION_MAX["context_selection"], signals=["stub"]
            ),
            "diff_minimality": DimensionScore(
                score=4, max_score=DIMENSION_MAX["diff_minimality"], signals=["stub"]
            ),
        }
        return ScoreReport(
            total=52,
            dimensions=partial,
            strengths=[],
            weaknesses=[],
            missed_failure_mode=False,
            badges_earned=[],
        )

    monkeypatch.setattr(runner_mod, "compute_score", _stub_compute_score)

    # Capture loguru warnings — loguru doesn't integrate with caplog by
    # default, so add a sink writing into a plain list and assert on it.
    from loguru import logger as _logger

    sink_lines: list[str] = []
    sink_id = _logger.add(lambda m: sink_lines.append(str(m)), level="WARNING")
    try:
        async with session_factory() as db:
            session = await db.get(SessionRow, session_id)
            runner = GradingRunner(settings=None, budget_seconds=30)
            submission, _result = await runner.run_and_persist(
                db=db,
                session=session,
                driver=driver,
                handle=handle,
                manifest=manifest,
                manifest_folder=folder,
                manifest_sha256="deadbeef" * 8,
            )
    finally:
        _logger.remove(sink_id)

    # The submission.graded event must carry all 7 dimensions.
    async with session_factory() as db:
        graded_rows = (
            (
                await db.execute(
                    select(SupervisionEvent).where(
                        SupervisionEvent.session_id == session_id,
                        SupervisionEvent.event_type == "submission.graded",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(graded_rows) == 1
        breakdown = (graded_rows[0].payload or {}).get("breakdown", {})
        assert set(breakdown.keys()) == {
            "final_correctness",
            "verification",
            "agent_review",
            "prompt_quality",
            "context_selection",
            "safety",
            "diff_minimality",
        }
        # The missing dimensions are zeroed with the correct max + a marker.
        assert breakdown["safety"]["score"] == 0
        assert breakdown["safety"]["max"] == 10
        assert "dimension_missing" in breakdown["safety"]["signals"]
        assert breakdown["prompt_quality"]["score"] == 0
        assert breakdown["prompt_quality"]["max"] == 10
        assert "dimension_missing" in breakdown["prompt_quality"]["signals"]
        # The dimensions that *were* returned retain their values.
        assert breakdown["final_correctness"]["score"] == 20

    # And a structured warning fired so operators see the drift.
    assert any(
        "missing dimensions" in line and "safety" in line and "prompt_quality" in line
        for line in sink_lines
    ), sink_lines

    # Sanity: the submission row's total_score was preserved (the runner
    # doesn't recompute totals when backfilling — it just guarantees the
    # event payload shape).
    assert submission.total_score == 52
