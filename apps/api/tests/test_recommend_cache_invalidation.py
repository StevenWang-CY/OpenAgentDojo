"""P1-2 — cache invalidation contract.

When a user grades a new submission, the recommendation cache must be
flagged stale so the next call recomputes. The contract is enforced by
:func:`app.recommendations.cache.invalidate_for_user`; this test asserts
the helper stamps ``invalidated_at`` on the existing row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.user import User
from app.models.user_recommendation import UserRecommendation
from app.recommendations.cache import invalidate_for_user


async def _bind_engine(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_invalidate_marks_row_stale(db_engine) -> None:
    session_local = await _bind_engine(db_engine)
    user_id = uuid.uuid4()
    async with session_local() as db:
        db.add(
            User(
                id=user_id,
                email=f"rec-{user_id.hex[:6]}@test.local",
                handle=f"rec-{user_id.hex[:4]}",
                session_epoch=1,
            )
        )
        db.add(
            UserRecommendation(
                user_id=user_id,
                weakest_dim="agent_review",
                recommended_ids=["auth-cookie-expiration", "agent-wrong-file"],
                computed_at=datetime(2026, 5, 27, 11, 0, 0, tzinfo=UTC),
                invalidated_at=None,
            )
        )
        await db.commit()

    async with session_local() as db:
        await invalidate_for_user(db, user_id)
        await db.commit()

    async with session_local() as db:
        row = (
            await db.execute(
                select(UserRecommendation).where(
                    UserRecommendation.user_id == user_id
                )
            )
        ).scalar_one()
    assert row.invalidated_at is not None
    assert row.weakest_dim == "agent_review"  # untouched


@pytest.mark.asyncio
async def test_invalidate_is_noop_for_missing_user(db_engine) -> None:
    """A user with no cached row tolerates the invalidation call.

    The helper is fired on every graded submission so a brand-new user
    has no row to update yet. The UPDATE simply matches zero rows.
    """
    session_local = await _bind_engine(db_engine)
    user_id = uuid.uuid4()
    async with session_local() as db:
        # Should not raise.
        await invalidate_for_user(db, user_id)
        await db.commit()
    async with session_local() as db:
        row = (
            await db.execute(
                select(UserRecommendation).where(
                    UserRecommendation.user_id == user_id
                )
            )
        ).scalar_one_or_none()
    assert row is None
