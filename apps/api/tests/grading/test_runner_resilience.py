"""GradingRunner resilience contracts.

Covers two narrow guarantees that the submit flow MUST honour:

* **P0-2** — if :func:`app.grading.diagnostics.compute_critical_moments`
  raises (malformed event payload, new variant the heuristics don't
  understand, etc.) the runner persists the submission with
  ``critical_moments == []`` instead of dropping the grade on the
  floor.
* **P0-3** — if the user row backing the session has vanished by the
  time grading reaches the verification stage (account deletion race,
  tombstoned user pointed at by an old session) the runner persists
  the submission with NULL verification hash + signature instead of
  raising. The report endpoint already 404s on NULL.

Both tests reuse the canned-driver fixtures from ``test_grading_runner``
to keep the fixture surface narrow and the scoring path identical to
the happy-path test that already exists.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete
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
    return "\n".join(blocks) + "\n" + _IDEAL_REGRESSION_TEST_PATCH


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
        if "requireAuth" in path:
            return b"export function requireAuth(req, res, next) { next(); }"
        return b""


class _FakeHandle:
    def __init__(self) -> None:
        self.workdir = Path("/nonexistent-workdir-for-test")


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_mission_and_session(
    session_factory, mission_id: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Return (session_id, user_id) — exposing the user id lets P0-3 delete it."""
    async with session_factory() as db:
        user_id = uuid.uuid4()
        user = User(
            id=user_id,
            email=f"resilience-{mission_id}@arena.local",
            display_name="Resilience",
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
        session = SessionRow(user_id=user_id, mission_id=mission.id, status="active")
        db.add(session)
        await db.commit()
        return session.id, user_id


async def _seed_strong_events(
    db,
    session_id: uuid.UUID,
    manifest_required: list[str],
    manifest_recommended: list[str],
) -> None:
    """Seed the minimum events the runner needs to score the ideal diff."""
    base = datetime.now(UTC) - timedelta(minutes=10)

    def at(off_s: int) -> datetime:
        return base + timedelta(seconds=off_s)

    rows = [
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
        SupervisionEvent(
            session_id=session_id,
            event_type="patch.applied",
            payload={"turn_index": 0, "files_changed": 2, "added": 18, "removed": 4},
            occurred_at=at(80),
        ),
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
    ]
    for r in rows:
        db.add(r)
    db.add(
        AgentTurn(
            session_id=session_id,
            turn_index=0,
            user_prompt="please fix the cookie expiration bug",
            selected_context={"files": manifest_required},
            agent_response="seed",
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_runner_persists_submission_when_critical_moments_raises(
    session_factory, monkeypatch
) -> None:
    """P0-2: ``compute_critical_moments`` raises → submission still persists.

    The diagnostic step is best-effort; the grade is the durable record.
    The runner must swallow the exception, log it, and degrade
    ``critical_moments`` to an empty list rather than dropping the row.
    """
    from app.grading import diagnostics as diagnostics_mod

    def _boom(*_args, **_kwargs):
        raise RuntimeError("malformed event payload")

    # The runner imports ``compute_critical_moments`` lazily inside
    # ``run_and_persist`` (``from app.grading.diagnostics import …``), so
    # the only place we can swap it in is the source module.
    monkeypatch.setattr(diagnostics_mod, "compute_critical_moments", _boom)

    manifest, folder = _load_mission01_manifest()
    diff = _ideal_diff_text(folder)
    driver = _CannedDriver(diff=diff)
    handle = _FakeHandle()

    session_id, _user_id = await _seed_mission_and_session(session_factory, manifest.id)
    async with session_factory() as db:
        await _seed_strong_events(
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

    assert submission.critical_moments == []
    # The grade itself still landed; the session flipped to graded.
    async with session_factory() as db:
        refreshed = await db.get(SessionRow, session_id)
        assert refreshed.status == "graded"


@pytest.mark.asyncio
async def test_runner_persists_submission_when_user_row_missing(
    session_factory,
) -> None:
    """P0-3: deleted user row → submission persists with NULL hash/sig.

    Simulates an account-deletion race: the session was started by a
    user that was tombstoned out before grading completes. The runner
    must skip the verification envelope (no subject to sign for) and
    persist the row with NULL columns rather than raise.
    """
    manifest, folder = _load_mission01_manifest()
    diff = _ideal_diff_text(folder)
    driver = _CannedDriver(diff=diff)
    handle = _FakeHandle()

    session_id, user_id = await _seed_mission_and_session(session_factory, manifest.id)
    async with session_factory() as db:
        await _seed_strong_events(
            db,
            session_id,
            manifest_required=list(manifest.expected_context.required),
            manifest_recommended=list(manifest.expected_context.recommended),
        )
        # Delete the user row to simulate the race.
        await db.execute(delete(User).where(User.id == user_id))
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

    assert submission.verification_hash is None
    assert submission.verification_signature is None
    async with session_factory() as db:
        refreshed = await db.get(SessionRow, session_id)
        assert refreshed.status == "graded"
