"""P4.2 — cache hit MUST be byte-identical to the cold write.

The cache rebuild path used to silently recompute alignment against
the user's *current* radar, which drifts between the original miss
and the next hit and produces non-deterministic "why" copy for the
same recommendation row. Migration 0027 added a JSON ``extras``
column that persists the per-item alignment computed at engine-call
time so the rebuild can rehydrate the original ranking signal.

This test pins the contract: calling
:func:`app.recommendations.cache.get_cached_or_compute` twice with
the same inputs returns ``RecommendationItem``s that are equal on
every load-bearing field (mission_id, title, language, difficulty,
why, your_best_score, your_attempts, status, target_release_date).
Only the metadata flags (``cache_hit``, ``computed_at``) are allowed
to differ — those are the cache-vs-fresh signal the FE renders for
debugging.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.repo_pack import RepoPack
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.recommendations.cache import get_cached_or_compute


async def _bind_engine(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_catalogue_and_history(session_local, user_id: uuid.UUID) -> None:
    """Seed a TS repo pack + four missions + one graded submission.

    The user's only graded submission ships a score-report whose
    ``agent_review`` dimension is very low so the engine's argmin
    picks ``agent_review`` as the weakest dim. That weakest-dim then
    drives the alignment scoring for every candidate.
    """
    async with session_local() as db:
        db.add(
            RepoPack(
                id="fullstack-auth-demo",
                title="TS pack",
                language="typescript",
                stack_summary="ts",
                repo_sha="0" * 40,
            )
        )
        db.add(
            User(
                id=user_id,
                email=f"fid-{user_id.hex[:6]}@test.local",
                handle=f"fid-{user_id.hex[:4]}",
                session_epoch=1,
            )
        )

        missions = [
            ("auth-cookie-expiration", "agent_review", "beginner",
             ("checks_presence_not_expiration",)),
            ("agent-wrong-file", "context_selection", "beginner",
             ("wrong_layer_committed",)),
            ("missing-regression-test", "verification", "intermediate",
             ("missing_regression_test",)),
            ("overfitted-test-fix", "final_correctness", "intermediate",
             ("overfitted_visible_test",)),
        ]
        for mid, weak_dim, difficulty, tags in missions:
            db.add(
                Mission(
                    id=mid,
                    title=f"Mission {mid}",
                    difficulty=difficulty,
                    category="testing",
                    repo_pack="fullstack-auth-demo",
                    repo_pack_id="fullstack-auth-demo",
                    initial_commit="abc1234",
                    estimated_minutes=20,
                    failure_mode=tags[0] if tags else "test",
                    skills_tested=[],
                    tags=list(tags),
                    expected_weak_dim=weak_dim,
                    manifest_sha256="0" * 64,
                    version=1,
                    published=True,
                    kind="standard",
                )
            )
        await db.flush()

        # One graded submission whose dimensions push ``agent_review``
        # to be the weakest dim. The mission's score is below the pass
        # threshold so the engine keeps it in the eligible set.
        session_row = SessionRow(
            id=uuid.uuid4(),
            user_id=user_id,
            mission_id="auth-cookie-expiration",
            status="graded",
            score=55,
            completed_at=datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC),
        )
        db.add(session_row)
        await db.flush()
        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=session_row.id,
                final_diff="",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={
                    "total": 55,
                    "is_stub": False,
                    "dimensions": {
                        "final_correctness": {"score": 25, "max": 30},
                        "verification": {"score": 12, "max": 15},
                        # 4/15 ~ 0.27 — lowest ratio, drives the argmin.
                        "agent_review": {"score": 4, "max": 15},
                        "prompt_quality": {"score": 8, "max": 10},
                        "context_selection": {"score": 8, "max": 10},
                        "safety": {"score": 9, "max": 10},
                        "diff_minimality": {"score": 9, "max": 10},
                    },
                },
                total_score=55,
                manifest_sha256="0" * 64,
                critical_moments=[],
            )
        )
        await db.commit()


def _item_to_comparable(item) -> dict:
    """Project a ``RecommendationItem`` onto the fields that must match.

    Excludes ``cache_hit`` / ``computed_at`` (set at the
    RecommendationSet level, not on the item) — those are the only
    permitted drift between cold-write and cache-hit responses.
    """
    return {
        "mission_id": item.mission_id,
        "title": item.title,
        "language": item.language,
        "difficulty": item.difficulty,
        "why": item.why,
        "your_best_score": item.your_best_score,
        "your_attempts": item.your_attempts,
        "status": item.status,
        "target_release_date": item.target_release_date,
    }


@pytest.mark.asyncio
async def test_cache_hit_is_byte_identical_to_cold_write(db_engine) -> None:
    """Cold-write → cache-hit returns identical items modulo metadata."""
    session_local = await _bind_engine(db_engine)
    user_id = uuid.uuid4()
    await _seed_catalogue_and_history(session_local, user_id)

    pinned_now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)

    async with session_local() as db:
        first = await get_cached_or_compute(db, user_id, now=pinned_now)
        await db.commit()
        assert first.cache_hit is False, "first call must be a cold write"
        assert len(first.recommendations) > 0, (
            "fixture must surface at least one recommendation"
        )

    async with session_local() as db:
        second = await get_cached_or_compute(db, user_id, now=pinned_now)
        await db.commit()
        assert second.cache_hit is True, "second call must be a cache hit"

    # Set-level invariants.
    assert first.weakest_dim == second.weakest_dim
    assert first.diagnosis == second.diagnosis
    assert len(first.recommendations) == len(second.recommendations)

    # Item-by-item byte-identical projection.
    for a, b in zip(first.recommendations, second.recommendations, strict=True):
        assert _item_to_comparable(a) == _item_to_comparable(b), (
            "cache hit drifted from cold write on item "
            f"{a.mission_id}: cold={_item_to_comparable(a)} "
            f"hit={_item_to_comparable(b)}"
        )


@pytest.mark.asyncio
async def test_cache_hit_preserves_why_copy_for_partial_alignment(
    db_engine,
) -> None:
    """The 0.5 alignment branch (failure-mode tag mapped) must persist.

    Without ``extras``, the rebuild path would re-derive alignment
    from the live catalogue + a possibly-drifted radar, producing a
    different ``why`` string than the cold write.  This test pins the
    persistence layer by asserting the ``why`` field round-trips
    exactly even when the alignment path is the partial 0.5 branch.
    """
    session_local = await _bind_engine(db_engine)
    user_id = uuid.uuid4()
    await _seed_catalogue_and_history(session_local, user_id)

    pinned_now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)

    async with session_local() as db:
        cold = await get_cached_or_compute(db, user_id, now=pinned_now)
        await db.commit()

    cold_whys = {r.mission_id: r.why for r in cold.recommendations}

    async with session_local() as db:
        warm = await get_cached_or_compute(db, user_id, now=pinned_now)
        await db.commit()

    warm_whys = {r.mission_id: r.why for r in warm.recommendations}
    assert cold_whys == warm_whys, (
        "rebuild produced different ``why`` copy than the cold write — "
        "extras column may be missing per-item alignment"
    )
