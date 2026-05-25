"""P0-1 — tutorial mission short-circuit.

The grading runner's standard pipeline must REJECT a tutorial mission
(``run_and_persist`` raises) and the new ``complete_tutorial`` method
must persist tutorial completion without ever instantiating a
Submission row.

These are pure-logic checks: no sandbox handle, no scoring, just the
runner's branching behaviour.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.grading.runner import GradingRunner
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User


@dataclass
class _StubManifest:
    """Minimal manifest stand-in — only ``kind`` is read by the runner."""

    id: str
    kind: str = "standard"


@pytest_asyncio.fixture
async def tutorial_session(db_session: AsyncSession) -> SessionRow:
    """Provision a user + session pointing at the orientation mission."""
    from app.models.mission import Mission

    user = User(
        email=f"u{uuid.uuid4().hex[:8]}@example.com",
        handle=f"u{uuid.uuid4().hex[:8]}",
    )
    mission = Mission(
        id="orientation",
        title="Orientation",
        difficulty="beginner",
        category="tutorial",
        repo_pack="fullstack-auth-demo",
        initial_commit="HEAD",
        estimated_minutes=8,
        failure_mode="missing_argument_concatenation",
        skills_tested=["workflow"],
        manifest_sha256="0" * 64,
        version=1,
        published=False,
        kind="tutorial",
    )
    db_session.add_all([user, mission])
    await db_session.flush()

    session = SessionRow(
        user_id=user.id,
        mission_id="orientation",
        status="active",
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest.mark.asyncio
async def test_run_and_persist_rejects_tutorial_manifest(
    db_session: AsyncSession,
    tutorial_session: SessionRow,
) -> None:
    runner = GradingRunner(settings=None)
    manifest = _StubManifest(id="orientation", kind="tutorial")
    with pytest.raises(RuntimeError, match="tutorial"):
        await runner.run_and_persist(
            db=db_session,
            session=tutorial_session,
            driver=None,
            handle=None,
            manifest=manifest,
            manifest_folder=None,  # type: ignore[arg-type]
            manifest_sha256="deadbeef" * 8,
        )


@pytest.mark.asyncio
async def test_complete_tutorial_persists_user_completion(
    db_session: AsyncSession,
    tutorial_session: SessionRow,
) -> None:
    runner = GradingRunner(settings=None)
    manifest = _StubManifest(id="orientation", kind="tutorial")

    before = datetime.now(UTC)
    await runner.complete_tutorial(
        db=db_session,
        session=tutorial_session,
        manifest=manifest,
    )

    # Session is graded with the sentinel score.
    assert tutorial_session.status == "graded"
    assert tutorial_session.score == 100

    # User row now has tutorial_completed_at set.
    user = (
        await db_session.execute(
            select(User).where(User.id == tutorial_session.user_id)
        )
    ).scalar_one()
    assert user.tutorial_completed_at is not None
    # SQLite serialises TIMESTAMPTZ as naive UTC strings, so the readback
    # may be tz-naive even though we wrote tz-aware. Normalise before
    # comparing.
    completed_at = user.tutorial_completed_at
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)
    assert completed_at >= before

    # No Submission row exists — tutorials don't grade.
    rows = (
        await db_session.execute(
            select(Submission).where(Submission.session_id == tutorial_session.id)
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_complete_tutorial_rejects_standard_manifest(
    db_session: AsyncSession,
    tutorial_session: SessionRow,
) -> None:
    runner = GradingRunner(settings=None)
    manifest = _StubManifest(id="orientation", kind="standard")
    with pytest.raises(RuntimeError, match="non-tutorial"):
        await runner.complete_tutorial(
            db=db_session,
            session=tutorial_session,
            manifest=manifest,
        )
