"""P1 — the all-graded cache hit MUST reproduce the cold write.

The "every mission graded" edge surfaces a single largest-gap retry
target plus two ``coming_soon`` roadmap placeholders. The placeholders
are NOT in the published mission catalogue, so the warm rebuild used to
resolve them against ``by_id`` (published missions only), miss, and
silently drop them — the user saw three cards cold but one card warm.
The surviving retry target was also rebuilt without the
``mode="all_graded"`` signal the cold path used, so its intermediate
"why" copy diverged before the prose polish overwrite.

This test pins the contract: a cold all-graded write and the subsequent
warm rebuild return byte-identical cards — same count, ids, statuses,
and "why" copy. It fails before the fix (warm collapses to one card).
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
from app.recommendations import cache as cache_module
from app.recommendations.cache import get_cached_or_compute
from app.recommendations.engine import _PlaceholderCandidate


async def _bind_engine(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


# Two passing missions; the user clears the pass threshold on BOTH so the
# engine's eligible set is empty and the all-graded branch fires.
_MISSIONS = (
    ("auth-cookie-expiration", "agent_review", "beginner", ("checks_presence_not_expiration",)),
    ("missing-regression-test", "verification", "intermediate", ("missing_regression_test",)),
)


async def _seed_all_graded(session_local, user_id: uuid.UUID) -> None:
    """Seed two missions the user has graded and PASSED.

    Each mission carries a graded submission scoring well above 70% of
    its effective max, so :func:`_eligible_candidates` returns nothing and
    ``recommend`` takes the all-graded branch.
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
                email=f"ag-{user_id.hex[:6]}@test.local",
                handle=f"ag-{user_id.hex[:4]}",
                session_epoch=1,
            )
        )
        for mid, weak_dim, difficulty, tags in _MISSIONS:
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

        for mid, _weak_dim, _difficulty, _tags in _MISSIONS:
            session_row = SessionRow(
                id=uuid.uuid4(),
                user_id=user_id,
                mission_id=mid,
                status="graded",
                score=92,
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
                        "total": 92,
                        "effective_max": 100,
                        "is_stub": False,
                        "dimensions": {
                            "final_correctness": {"score": 28, "max": 30},
                            "verification": {"score": 14, "max": 15},
                            "agent_review": {"score": 13, "max": 15},
                            "prompt_quality": {"score": 9, "max": 10},
                            "context_selection": {"score": 9, "max": 10},
                            "safety": {"score": 9, "max": 10},
                            "diff_minimality": {"score": 9, "max": 10},
                        },
                    },
                    total_score=92,
                    manifest_sha256="0" * 64,
                    critical_moments=[],
                )
            )
        await db.commit()


def _placeholders() -> list[_PlaceholderCandidate]:
    """Two deterministic coming-soon placeholders (not in the catalogue)."""
    return [
        _PlaceholderCandidate(
            mission_id="future-mission-alpha",
            title="Future Mission Alpha",
            language="python",
            target_release_date="2026-12-01",
        ),
        _PlaceholderCandidate(
            mission_id="future-mission-beta",
            title="Future Mission Beta",
            language="go",
            target_release_date="2027-01-15",
        ),
    ]


def _item_to_comparable(item) -> dict:
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
async def test_all_graded_cache_hit_matches_cold_write(db_engine, monkeypatch) -> None:
    """Cold all-graded write → warm rebuild: identical cards, no drops."""
    session_local = await _bind_engine(db_engine)
    user_id = uuid.uuid4()
    await _seed_all_graded(session_local, user_id)

    # Pin the roadmap placeholders so the all-graded path is deterministic
    # and independent of the real (date-gated) roadmap.yaml.
    monkeypatch.setattr(cache_module, "_coming_soon_from_roadmap", _placeholders)

    pinned_now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)

    async with session_local() as db:
        cold = await get_cached_or_compute(db, user_id, now=pinned_now)
        await db.commit()

    assert cold.cache_hit is False, "first call must be a cold write"
    # One shipped retry target + two coming-soon placeholders.
    assert len(cold.recommendations) == 3, (
        "fixture must surface one retry target plus two coming-soon cards"
    )
    statuses = [r.status for r in cold.recommendations]
    assert statuses == ["shipped", "coming_soon", "coming_soon"], statuses
    placeholder_ids = {r.mission_id for r in cold.recommendations if r.status == "coming_soon"}
    assert placeholder_ids == {"future-mission-alpha", "future-mission-beta"}

    async with session_local() as db:
        warm = await get_cached_or_compute(db, user_id, now=pinned_now)
        await db.commit()

    assert warm.cache_hit is True, "second call must be a cache hit"

    # Set-level invariants.
    assert warm.weakest_dim == cold.weakest_dim
    assert warm.diagnosis == cold.diagnosis
    assert len(warm.recommendations) == len(cold.recommendations), (
        "warm rebuild dropped cards — coming-soon placeholders vanished"
    )

    # Item-by-item byte-identical projection (count, ids, statuses, why).
    for a, b in zip(cold.recommendations, warm.recommendations, strict=True):
        assert _item_to_comparable(a) == _item_to_comparable(b), (
            "all-graded cache hit drifted from cold write on item "
            f"{a.mission_id}: cold={_item_to_comparable(a)} hit={_item_to_comparable(b)}"
        )
