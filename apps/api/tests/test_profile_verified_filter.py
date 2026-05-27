"""P0-8 — public profile partitions on ``submission.verified``.

A profile with at least one verified (proctored) best-per-mission attempt
defaults to the verified-only radar. A profile with only honor-mode
attempts surfaces the full set with ``has_verified_attempts=False`` so
the FE can render the "// honor-mode only" notice instead of pretending
the surface is a credential.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.profiles.router import _fetch_stats


async def _bound_session(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_user_and_missions(SessionLocal) -> tuple[uuid.UUID, list[str]]:  # noqa: N803
    user_id = uuid.uuid4()
    mission_ids = ["mode-mission-a", "mode-mission-b"]
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email=f"{user_id.hex[:8]}@test.local",
                display_name="Test User",
                handle=f"user-{user_id.hex[:8]}",
            )
        )
        for mid in mission_ids:
            db.add(
                Mission(
                    id=mid,
                    title=mid.title(),
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
            )
        await db.commit()
    return user_id, mission_ids


async def _persist_attempt(
    SessionLocal,  # noqa: N803
    *,
    user_id: uuid.UUID,
    mission_id: str,
    score: int,
    verified: bool,
    mode: str,
    attempt_index: int = 1,
    completed_offset_minutes: int = 0,
) -> uuid.UUID:
    session_id = uuid.uuid4()
    submission_id = uuid.uuid4()
    completed_at = datetime.now(UTC) + timedelta(minutes=completed_offset_minutes)
    async with SessionLocal() as db:
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id=mission_id,
                status="graded",
                attempt_index=attempt_index,
                score=score,
                completed_at=completed_at,
                started_at=completed_at - timedelta(minutes=15),
                mode=mode,
            )
        )
        db.add(
            Submission(
                id=submission_id,
                session_id=session_id,
                final_diff="",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={
                    "total": score,
                    "dimensions": {
                        "final_correctness": {
                            "score": 20,
                            "max": 30,
                            "signals": [],
                        },
                        "verification": {"score": 10, "max": 15, "signals": []},
                        "agent_review": {"score": 10, "max": 15, "signals": []},
                        "prompt_quality": {"score": 7, "max": 10, "signals": []},
                        "context_selection": {
                            "score": 7,
                            "max": 10,
                            "signals": [],
                        },
                        "safety": {"score": 8, "max": 10, "signals": []},
                        "diff_minimality": {"score": 8, "max": 10, "signals": []},
                    },
                    "strengths": [],
                    "weaknesses": [],
                    "missed_failure_mode": False,
                    "badges_earned": [],
                    "effective_max": 100,
                    "is_stub": False,
                },
                total_score=score,
                verified=verified,
            )
        )
        await db.commit()
    return session_id


@pytest.mark.asyncio
async def test_fetch_stats_defaults_to_verified_only_when_present(db_engine) -> None:
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_ids = await _seed_user_and_missions(SessionLocal)

    # mission-a: a verified (proctored) attempt @ 80.
    await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_ids[0],
        score=80,
        verified=True,
        mode="proctored",
        completed_offset_minutes=0,
    )
    # mission-b: a self-study attempt @ 90 — must NOT appear in the
    # verified-only radar even though its score is higher.
    await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_ids[1],
        score=90,
        verified=False,
        mode="self_study",
        completed_offset_minutes=5,
    )

    async with SessionLocal() as db:
        (
            total_missions,
            best_score,
            radar,
            verified_radar,
            has_verified,
            verified_only,
        ) = await _fetch_stats(db, user_id)

    assert total_missions == 2
    assert has_verified is True
    assert verified_only is True
    # The radar reflects the verified bucket — only the proctored
    # mission-a attempt contributes, so its dimension averages match
    # that single attempt's dimension scores.
    assert verified_radar is not None
    assert verified_radar.get("final_correctness") == 20.0
    # Radar and verified_radar are the same dict in the
    # ``has_verified_attempts=True`` branch.
    assert radar == verified_radar


@pytest.mark.asyncio
async def test_fetch_stats_falls_back_to_all_when_no_verified(db_engine) -> None:
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_ids = await _seed_user_and_missions(SessionLocal)

    # Two honor-mode attempts; no verified attempts on file.
    await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_ids[0],
        score=70,
        verified=False,
        mode="self_study",
    )
    await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_ids[1],
        score=80,
        verified=False,
        mode="self_study",
        completed_offset_minutes=5,
    )

    async with SessionLocal() as db:
        (
            total_missions,
            best_score,
            radar,
            verified_radar,
            has_verified,
            verified_only,
        ) = await _fetch_stats(db, user_id)

    assert total_missions == 2
    assert has_verified is False
    assert verified_only is False
    assert verified_radar is None
    # The radar must still be populated — fall back to every attempt —
    # so the public surface isn't blank for an honor-mode-only profile.
    assert radar.get("final_correctness") == 20.0
