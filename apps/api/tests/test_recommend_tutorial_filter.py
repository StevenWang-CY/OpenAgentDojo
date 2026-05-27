"""Tutorial submissions don't count toward graded_count (P4.1 audit fix).

The recommendation engine's cold-start ladder is keyed off
``UserHistory.graded_count``. Prior to this fix, the loader joined
``submissions`` against ``sessions`` and counted *every* graded
session — including tutorial completions, which short-circuit the
runner with a sentinel ``score=100`` (see
``GradingRunner.complete_tutorial``). One tutorial completion was
therefore enough to push the user out of cold-start, and the engine
would surface a ranked list against an empty radar.

The fix joins against ``missions`` and filters ``kind != 'tutorial'``.
This test pins the contract: a user whose only graded submission is
the tutorial must read as ``graded_count == 0``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.db.base import Base
from app.models.mission import Mission
from app.models.repo_pack import RepoPack
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.recommendations.cache import load_user_history


@pytest.mark.asyncio
async def test_tutorial_submission_does_not_count_toward_graded_count(
    db_engine,
) -> None:
    """A graded tutorial must read as cold-start (graded_count == 0)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async with Session() as db:
        # Seed: one repo pack + one tutorial mission + one user.
        db.add(
            RepoPack(
                id="fullstack-auth-demo",
                title="ts pack",
                language="typescript",
                stack_summary="ts",
                repo_sha="0" * 40,
            )
        )
        await db.flush()

        tutorial = Mission(
            id="tutorial-start-here",
            title="Tutorial",
            difficulty="beginner",
            category="tutorial",
            repo_pack="fullstack-auth-demo",
            repo_pack_id="fullstack-auth-demo",
            initial_commit="abc1234",
            estimated_minutes=5,
            failure_mode="introduction",
            skills_tested=[],
            tags=[],
            # Tutorials carry NULL ``expected_weak_dim`` per the
            # ``missions_kind_weak_dim_required`` constraint.
            expected_weak_dim=None,
            manifest_sha256="0" * 64,
            version=1,
            published=True,
            kind="tutorial",
        )
        db.add(tutorial)
        user_id = uuid.uuid4()
        db.add(
            User(
                id=user_id,
                email=f"tut-{user_id.hex[:6]}@test.local",
                handle=f"tut-{user_id.hex[:4]}",
                session_epoch=1,
            )
        )
        await db.flush()

        # Tutorial completion: sentinel score=100, graded status.
        session_row = SessionRow(
            id=uuid.uuid4(),
            user_id=user_id,
            mission_id=tutorial.id,
            status="graded",
            score=100,
            completed_at=datetime.now(UTC),
        )
        db.add(session_row)
        await db.flush()
        # Plausible payload — the runner doesn't actually persist a
        # ``Submission`` for tutorial completions (see
        # ``complete_tutorial`` in ``app/grading/runner.py``), but we
        # still write one here to prove the filter is by ``kind`` and
        # not by "tutorial has no submission".
        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=session_row.id,
                final_diff="",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={"total": 100, "dimensions": {}, "is_stub": False},
                total_score=100,
                manifest_sha256="0" * 64,
                critical_moments=[],
            )
        )
        await db.commit()

        history = await load_user_history(db, user_id)
        assert history.graded_count == 0, (
            "tutorial completions must not count toward graded_count — "
            "cold-start ladder otherwise breaks for first-time users"
        )
        assert history.best_attempts == {}
