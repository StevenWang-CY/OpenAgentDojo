"""Phase 4.A.T7 — verification hash round-trips across ``created_at`` reload.

The grading runner pins ``submission.created_at`` to a UTC second
(rounded via ``replace(microsecond=0)``) so the envelope's
``graded_at`` field — computed from the same datetime — matches the
on-disk row byte-for-byte when reloaded. Without that pin, the
runner's in-memory datetime carries microseconds the DB column then
strips, the envelope re-derives a different ISO string, and the
verification hash mismatches forever.

Test: insert a Submission row with an explicit ``created_at``, refresh
from the DB, rebuild the envelope, compute the hash, and assert it
equals the value computed from the original (pre-INSERT) row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.reports.verification import build_envelope, compute_hash


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


class _Manifest:
    id = "round-trip-mission"
    title = "Round trip"
    version = 1


@pytest.mark.asyncio
async def test_created_at_roundtrip_matches_hash(session_factory) -> None:
    user_id = uuid.uuid4()
    submission_id = uuid.uuid4()
    graded_at = datetime.now(UTC).replace(microsecond=0)

    async with session_factory() as db:
        user = User(
            id=user_id,
            email=f"rt-{user_id.hex[:8]}@a.local",
            handle=f"rt-{user_id.hex[:6]}",
            session_epoch=1,
        )
        db.add(user)
        session = SessionRow(
            user_id=user_id,
            mission_id=_Manifest.id,
            status="graded",
            mode="proctored",
            attempt_index=1,
        )
        db.add(session)
        await db.flush()
        submission = Submission(
            id=submission_id,
            session_id=session.id,
            final_diff="",
            visible_test_results=[],
            hidden_test_results=[],
            validator_results=[],
            score_report={"total": 70, "effective_max": 100, "missed_failure_mode": False},
            total_score=70,
            verified=True,
            created_at=graded_at,
        )
        db.add(submission)
        await db.commit()

        # Compute the hash from the IN-MEMORY row (matches what the
        # grading runner would have stamped at grade time).
        envelope_pre = build_envelope(
            submission=submission, session=session, manifest=_Manifest(), user=user
        )
        hash_pre = compute_hash(envelope_pre)

    # Reload from a fresh session — this is where microsecond drift
    # would have bitten if the runner hadn't pinned the value.
    async with session_factory() as db:
        row = (
            await db.execute(select(Submission).where(Submission.id == submission_id))
        ).scalar_one()
        sess = (
            await db.execute(select(SessionRow).where(SessionRow.id == row.session_id))
        ).scalar_one()
        usr = (await db.execute(select(User).where(User.id == sess.user_id))).scalar_one()
        envelope_post = build_envelope(submission=row, session=sess, manifest=_Manifest(), user=usr)
        hash_post = compute_hash(envelope_post)

    assert hash_pre == hash_post, (
        f"envelope hash drifted across reload — pre={hash_pre} post={hash_post}\n"
        f"pre_env={envelope_pre}\npost_env={envelope_post}"
    )
