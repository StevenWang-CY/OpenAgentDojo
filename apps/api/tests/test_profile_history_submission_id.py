"""Wave 2C — ``MissionHistoryItem.submission_id`` end-to-end coverage.

The account Data tab renders a per-row Replay button that calls
``downloadReplayZip(submission_id)``. The button only renders when the
history row carries a non-null ``submission_id``; this test pins the
contract end-to-end:

  * a graded session **with** a submission row -> ``submission_id`` is the
    producing ``submissions.id`` (so the FE Replay click resolves).
  * a graded session **without** a submission row (legacy / data-skew) ->
    ``submission_id`` is ``null`` (so the FE omits the affordance instead
    of wiring a 404).

The first path is the production contract; the second guards the
outer-join we deliberately kept in ``_fetch_history`` so a missing
submission row doesn't drop the entire history entry.
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


async def _bind_engine(db_engine):
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_module.AsyncSessionLocal


def _score_report() -> dict:
    """Minimal non-stub score_report so the history row isn't filtered out."""
    from app.grading.dimensions import DIMENSION_MAX

    return {
        "total": 70,
        "dimensions": {
            "final_correctness": {
                "score": 20,
                "max": DIMENSION_MAX["final_correctness"],
                "signals": [],
            },
        },
        "strengths": [],
        "weaknesses": [],
        "missed_failure_mode": False,
        "badges_earned": [],
    }


@pytest.mark.asyncio
async def test_history_populates_submission_id_for_graded_session(
    client, db_engine
) -> None:
    """A graded session with a joined submission surfaces submission_id."""
    AsyncSessionLocal = await _bind_engine(db_engine)

    user_id = uuid.uuid4()
    sess_id = uuid.uuid4()
    sub_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="carol@arena.local",
                handle="carol",
                display_name="Carol",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Auth Cookie Expiration",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=20,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha-aaa",
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        db.add(
            SessionRow(
                id=sess_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="graded",
                started_at=now,
                completed_at=now,
                score=70,
            )
        )
        await db.flush()
        db.add(
            Submission(
                id=sub_id,
                session_id=sess_id,
                final_diff="x",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report=_score_report(),
                total_score=70,
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/profiles/carol")
    assert resp.status_code == 200, resp.text
    history = resp.json()["history"]
    assert len(history) == 1
    row = history[0]
    assert row["session_id"] == str(sess_id)
    # The producing submission id is what the FE replay click keys on.
    assert row["submission_id"] == str(sub_id)


@pytest.mark.asyncio
async def test_history_submission_id_null_when_submission_row_missing(
    client, db_engine
) -> None:
    """An outer-joined row with no submission shows ``submission_id: null``.

    The query in ``_fetch_history`` deliberately outer-joins ``submissions``
    so a missing submission row doesn't drop the entry — but we MUST signal
    ``null`` so the FE renders the row without a (broken) Replay click.
    """
    AsyncSessionLocal = await _bind_engine(db_engine)

    user_id = uuid.uuid4()
    sess_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="dave@arena.local",
                handle="dave",
                display_name="Dave",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Auth Cookie Expiration",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=20,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha-bbb",
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        # Graded session with NO submission row — exercises the outer join.
        db.add(
            SessionRow(
                id=sess_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="graded",
                started_at=now - timedelta(hours=1),
                completed_at=now,
                score=42,
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/profiles/dave")
    assert resp.status_code == 200, resp.text
    history = resp.json()["history"]
    assert len(history) == 1
    row = history[0]
    assert row["session_id"] == str(sess_id)
    assert row["submission_id"] is None
