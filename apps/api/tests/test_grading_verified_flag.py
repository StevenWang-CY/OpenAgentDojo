"""P0-8 — grading runner stamps ``submission.verified`` from ``session.mode``.

A proctored session must produce a submission with ``verified=True``; a
self-study session must produce ``verified=False``. The verification
envelope's ``proctored`` field must mirror the same bool so the public
``/verify/{id}`` page can render the chip without a second lookup.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.grading.runner import GradingRunner
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.sandbox.types import GradingArtifacts


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


class _TrivialDriver:
    """Minimal driver: empty diff + green hidden tests, no validator work."""

    async def freeze_and_grade(self, handle, mission, *, manifest_folder=None):
        await asyncio.sleep(0)
        return GradingArtifacts(
            diff="",
            test_results={
                "unit": {
                    "suite": "unit",
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "timed_out": False,
                },
                "hidden": {
                    "suite": "hidden",
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": 1,
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


class _MinimalManifest:
    """In-memory stand-in — the runner reads .id, .validators, .hidden_tests, etc."""

    id = "m-verified"
    kind = "standard"
    validators: list = []
    expected_files: list[str] = []
    expected_context = None
    failure_mode = None
    version = 1

    class _HiddenTests:
        suites = ["hidden"]

    hidden_tests = _HiddenTests()

    title = "Verified mission"


async def _seed_session(session_factory, *, mode: str) -> uuid.UUID:
    async with session_factory() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"v-{mode}@a.local",
            display_name="V",
            handle=f"v-{mode}",
        )
        db.add(user)
        mission = Mission(
            id=_MinimalManifest.id,
            title="Verified mission",
            difficulty="beginner",
            category="cat",
            repo_pack="p",
            initial_commit="HEAD",
            estimated_minutes=10,
            failure_mode="f",
            skills_tested=["s"],
            manifest_sha256="sha",
            version=1,
            published=True,
            expected_weak_dim="safety",
        )
        db.add(mission)
        await db.flush()
        session = SessionRow(
            user_id=user.id,
            mission_id=mission.id,
            status="active",
            mode=mode,
        )
        db.add(session)
        await db.commit()
        return session.id


@pytest.mark.asyncio
async def test_proctored_session_grades_to_verified_true(session_factory) -> None:
    session_id = await _seed_session(session_factory, mode="proctored")
    driver = _TrivialDriver()

    async with session_factory() as db:
        session = await db.get(SessionRow, session_id)
        runner = GradingRunner(settings=None, budget_seconds=30)
        submission, result = await runner.run_and_persist(
            db=db,
            session=session,
            driver=driver,
            handle=_FakeHandle(),
            manifest=_MinimalManifest(),
            manifest_folder=Path("/tmp"),
            manifest_sha256="deadbeef" * 8,
        )

    assert result.verified is True
    async with session_factory() as db:
        persisted = await db.get(Submission, submission.id)
        assert persisted is not None
        assert persisted.verified is True


@pytest.mark.asyncio
async def test_self_study_session_grades_to_verified_false(session_factory) -> None:
    session_id = await _seed_session(session_factory, mode="self_study")
    driver = _TrivialDriver()

    async with session_factory() as db:
        session = await db.get(SessionRow, session_id)
        runner = GradingRunner(settings=None, budget_seconds=30)
        submission, result = await runner.run_and_persist(
            db=db,
            session=session,
            driver=driver,
            handle=_FakeHandle(),
            manifest=_MinimalManifest(),
            manifest_folder=Path("/tmp"),
            manifest_sha256="deadbeef" * 8,
        )

    assert result.verified is False
    async with session_factory() as db:
        persisted = await db.get(Submission, submission.id)
        assert persisted is not None
        assert persisted.verified is False


@pytest.mark.asyncio
async def test_verify_envelope_mirrors_submission_verified(session_factory) -> None:
    """The envelope builder reads ``submission.verified`` into ``proctored``."""
    from app.reports.verification import build_envelope

    # Use a stand-in submission object with the new field set.
    class _Sub:
        id = uuid.uuid4()
        total_score = 50
        score_cap_reason = None
        score_report: dict = {"effective_max": 100}
        verified = True
        created_at = None

    class _Session:
        mission_id = "m-x"
        attempt_index = 1

    envelope = build_envelope(
        submission=_Sub(),
        session=_Session(),
        manifest=None,
        user=None,
    )
    assert envelope["proctored"] is True

    class _SubFalse(_Sub):
        verified = False

    envelope_off = build_envelope(
        submission=_SubFalse(),
        session=_Session(),
        manifest=None,
        user=None,
    )
    assert envelope_off["proctored"] is False
