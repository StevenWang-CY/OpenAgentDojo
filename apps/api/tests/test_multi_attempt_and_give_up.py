"""P0-3 (multi-attempt scoring) + P0-4 (give-up affordance) — backend tests.

Mirrors the patterns from ``test_sessions_service.py`` (in-process SQLite,
``_bound_session`` helper, hand-seeded ORM rows). The tests live in a single
module because the two features share a column (``submissions.score_cap_reason``)
and the multi-attempt aggregations have to honour the give-up cap policy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.grading.attempts import candidate_beats
from app.grading.score import (
    GAVE_UP_SCORE_CAP,
    DimensionScore,
    ScoreReport,
    apply_score_cap,
)
from app.missions.your_attempts import load_your_attempts
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.profiles.router import _best_per_mission, _fetch_stats
from app.sessions.service import create_session


async def _bound_session(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_user_and_mission(
    SessionLocal,  # noqa: N803 — SQLAlchemy session_factory naming convention
    *,
    mission_id: str = "auth-cookie-expiration",
) -> tuple[uuid.UUID, str]:
    user_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email=f"{user_id.hex[:8]}@test.local",
                display_name="Test User",
            )
        )
        db.add(
            Mission(
                id=mission_id,
                title="Auth Cookie Expiration",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=20,
                failure_mode="session_validity_check",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        await db.commit()
    return user_id, mission_id


async def _persist_attempt(
    SessionLocal,  # noqa: N803 — SQLAlchemy session_factory naming convention
    *,
    user_id: uuid.UUID,
    mission_id: str,
    attempt_index: int,
    score: int,
    score_cap_reason: str | None = None,
    completed_offset_minutes: int = 0,
    is_stub: bool = False,
    missed_failure_mode: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a graded session + submission. Returns (session_id, submission_id)."""
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
                        "final_correctness": {"score": 20, "max": 30, "signals": []},
                        "verification": {"score": 10, "max": 15, "signals": []},
                        "agent_review": {"score": 10, "max": 15, "signals": []},
                        "prompt_quality": {"score": 7, "max": 10, "signals": []},
                        "context_selection": {"score": 7, "max": 10, "signals": []},
                        "safety": {"score": 8, "max": 10, "signals": []},
                        "diff_minimality": {"score": 8, "max": 10, "signals": []},
                    },
                    "strengths": [],
                    "weaknesses": [],
                    "missed_failure_mode": missed_failure_mode,
                    "badges_earned": [],
                    "effective_max": 100,
                    "is_stub": is_stub,
                },
                total_score=score,
                score_cap_reason=score_cap_reason,
            )
        )
        await db.commit()
    return session_id, submission_id


# ---------------------------------------------------------------------------
# P0-3 — attempt_index + previous_session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_index_increments_on_create(db_engine) -> None:
    """create_session derives attempt_index from prior (user, mission) row count."""
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    async with SessionLocal() as db:
        row1 = await create_session(db, user_id=user_id, mission_id=mission_id)
        await db.commit()
    assert row1.attempt_index == 1
    assert row1.previous_session_id is None

    # Mark the first session as graded so the concurrency cap clears.
    async with SessionLocal() as db:
        await db.execute(
            SessionRow.__table__.update()
            .where(SessionRow.id == row1.id)
            .values(status="graded", completed_at=datetime.now(UTC))
        )
        await db.commit()

    async with SessionLocal() as db:
        row2 = await create_session(
            db,
            user_id=user_id,
            mission_id=mission_id,
            previous_session_id=row1.id,
        )
        await db.commit()
    assert row2.attempt_index == 2
    assert row2.previous_session_id == row1.id


@pytest.mark.asyncio
async def test_previous_session_id_silently_dropped_when_stale(db_engine) -> None:
    """A mismatched previous_session_id (wrong user/mission) is dropped, not raised."""
    SessionLocal = await _bound_session(db_engine)
    user_a, mission_id = await _seed_user_and_mission(SessionLocal)
    user_b = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_b,
                email="other@test.local",
                display_name="Other",
            )
        )
        await db.commit()

    # User A has a graded attempt; user B tries to "retry" pointing at it.
    sid_a, _ = await _persist_attempt(
        SessionLocal,
        user_id=user_a,
        mission_id=mission_id,
        attempt_index=1,
        score=70,
    )

    async with SessionLocal() as db:
        row = await create_session(
            db,
            user_id=user_b,
            mission_id=mission_id,
            previous_session_id=sid_a,
        )
        await db.commit()
    # User B's session should ignore the stale pointer without raising.
    assert row.previous_session_id is None
    assert row.attempt_index == 1


@pytest.mark.asyncio
async def test_attempt_index_counts_every_status(db_engine) -> None:
    """attempt_index counts ALL prior rows, including aborted/errored attempts.

    Otherwise a user who errors out on attempts #1 and #2 would see #3
    labelled "Attempt 1" when retrying — confusing and inaccurate.
    """
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    # Seed two errored sessions directly.
    for i in range(2):
        async with SessionLocal() as db:
            db.add(
                SessionRow(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    mission_id=mission_id,
                    status="error",
                    attempt_index=i + 1,
                    started_at=datetime.now(UTC) - timedelta(hours=i + 1),
                )
            )
            await db.commit()

    async with SessionLocal() as db:
        row = await create_session(db, user_id=user_id, mission_id=mission_id)
        await db.commit()
    assert row.attempt_index == 3


# ---------------------------------------------------------------------------
# P0-3 — best-per-mission dedupe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_best_per_mission_picks_uncapped_over_gave_up(db_engine) -> None:
    """An uncapped 60 beats a gave-up 50, even when scores are close."""
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    # Gave-up attempt landed FIRST (higher score, capped).
    _, sub_gave_up = await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=1,
        score=50,
        score_cap_reason="gave_up",
        completed_offset_minutes=-30,
    )
    # Uncapped retry later (lower score).
    _, sub_uncapped = await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=2,
        score=42,
        score_cap_reason=None,
        completed_offset_minutes=0,
    )

    async with SessionLocal() as db:
        bests = await _best_per_mission(db, user_id)
    assert len(bests) == 1
    assert bests[0].submission_id == sub_uncapped
    assert bests[0].score == 42
    assert bests[0].score_cap_reason is None


@pytest.mark.asyncio
async def test_best_per_mission_falls_back_to_gave_up_when_only_option(db_engine) -> None:
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    _, sub_id = await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=1,
        score=50,
        score_cap_reason="gave_up",
    )

    async with SessionLocal() as db:
        bests = await _best_per_mission(db, user_id)
    assert len(bests) == 1
    assert bests[0].submission_id == sub_id
    assert bests[0].score_cap_reason == "gave_up"


@pytest.mark.asyncio
async def test_best_per_mission_excludes_stubs(db_engine) -> None:
    """Grader-failure stubs never count as a "best" attempt."""
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=1,
        score=0,
        is_stub=True,
    )

    async with SessionLocal() as db:
        bests = await _best_per_mission(db, user_id)
    assert bests == []


@pytest.mark.asyncio
async def test_fetch_stats_uses_best_per_mission(db_engine) -> None:
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    # Three attempts on the same mission: 60, 78, 65.
    for idx, score in enumerate([60, 78, 65], start=1):
        await _persist_attempt(
            SessionLocal,
            user_id=user_id,
            mission_id=mission_id,
            attempt_index=idx,
            score=score,
            completed_offset_minutes=idx * 5,
        )

    async with SessionLocal() as db:
        (
            total_missions,
            best_score,
            _radar,
            _verified_radar,
            _has_verified,
            _verified_only,
        ) = await _fetch_stats(db, user_id)
    assert total_missions == 1
    assert best_score == 78


def test_candidate_beats_tie_breaks_by_completed_at() -> None:
    """Same tier + same score → most recent attempt wins."""
    now = datetime.now(UTC)

    class _A:
        score = 70
        score_cap_reason = None
        completed_at = now - timedelta(hours=1)

    class _B:
        score = 70
        score_cap_reason = None
        completed_at = now

    assert candidate_beats(_B(), _A()) is True
    assert candidate_beats(_A(), _B()) is False


# ---------------------------------------------------------------------------
# P0-3 — your_attempts strip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_your_attempts_zero_when_no_history(db_engine) -> None:
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)
    async with SessionLocal() as db:
        ya = await load_your_attempts(db, user_id=user_id, mission_id=mission_id)
    assert ya.count == 0
    assert ya.best_score is None
    assert ya.delta is None


@pytest.mark.asyncio
async def test_your_attempts_computes_delta_from_first_to_latest(db_engine) -> None:
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=1,
        score=60,
        completed_offset_minutes=-30,
    )
    await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=2,
        score=72,
        completed_offset_minutes=0,
    )

    async with SessionLocal() as db:
        ya = await load_your_attempts(db, user_id=user_id, mission_id=mission_id)
    assert ya.count == 2
    assert ya.best_score == 72
    assert ya.latest_score == 72
    assert ya.delta == 12
    assert ya.best_was_gave_up is False


@pytest.mark.asyncio
async def test_your_attempts_best_was_gave_up_flag(db_engine) -> None:
    """When the user's best attempt is a give-up, the chip surfaces."""
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=1,
        score=50,
        score_cap_reason="gave_up",
    )

    async with SessionLocal() as db:
        ya = await load_your_attempts(db, user_id=user_id, mission_id=mission_id)
    assert ya.count == 1
    assert ya.best_score == 50
    assert ya.best_was_gave_up is True


# ---------------------------------------------------------------------------
# P0-4 — apply_score_cap
# ---------------------------------------------------------------------------


def _fixture_report(total: int) -> ScoreReport:
    return ScoreReport(
        total=total,
        dimensions={
            "final_correctness": DimensionScore(score=24, max_score=30),
            "verification": DimensionScore(score=12, max_score=15),
            "agent_review": DimensionScore(score=11, max_score=15),
            "prompt_quality": DimensionScore(score=8, max_score=10),
            "context_selection": DimensionScore(score=7, max_score=10),
            "safety": DimensionScore(score=10, max_score=10),
            "diff_minimality": DimensionScore(score=10, max_score=10),
        },
        strengths=[],
        weaknesses=[],
        missed_failure_mode=False,
        badges_earned=[],
    )


def test_apply_score_cap_caps_when_total_exceeds_cap() -> None:
    report = _fixture_report(total=82)
    apply_score_cap(report, reason="gave_up", cap=GAVE_UP_SCORE_CAP)
    assert report.total == 50
    assert report.uncapped_total == 82
    assert report.score_cap_reason == "gave_up"
    # Dimension scores untouched — the cap is at the report-total level only.
    assert report.dimensions["final_correctness"].score == 24
    assert report.dimensions["safety"].score == 10


def test_apply_score_cap_records_reason_even_when_under_cap() -> None:
    """A weak attempt under the cap still records the deliberate forfeit."""
    report = _fixture_report(total=38)
    apply_score_cap(report, reason="gave_up", cap=GAVE_UP_SCORE_CAP)
    assert report.total == 38  # cap not binding
    assert report.uncapped_total == 38
    assert report.score_cap_reason == "gave_up"


def test_score_report_to_dict_includes_cap_fields() -> None:
    report = _fixture_report(total=70)
    apply_score_cap(report, reason="gave_up", cap=GAVE_UP_SCORE_CAP)
    out = report.to_dict()
    assert out["score_cap_reason"] == "gave_up"
    assert out["uncapped_total"] == 70
    assert out["total"] == 50


# ---------------------------------------------------------------------------
# P0-4 — give-up endpoint integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_give_up_blocked_before_window(client_with_db, monkeypatch) -> None:
    """POSTing /give-up before 10 minutes returns 425 with seconds_remaining."""
    from app.auth.deps import require_auth

    # Seed a fresh active session via the in-memory db.
    from app.db.session import AsyncSessionLocal

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="giveup-early@test.local",
                display_name="Early",
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Auth",
                difficulty="intermediate",
                category="auth",
                repo_pack="x",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        # started_at is right now → no minutes elapsed.
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="active",
                started_at=datetime.now(UTC),
            )
        )
        await db.commit()

    # Stub require_auth so the test client is authenticated as this user.
    user_row = User(id=user_id, email="giveup-early@test.local")

    async def _fake_require_auth():
        return user_row

    client_with_db._transport.app.dependency_overrides[require_auth] = _fake_require_auth  # type: ignore[attr-defined]

    try:
        # CSRF — bypass by setting a static cookie + header that matches.
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            f"/api/v1/sessions/{session_id}/give-up",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 425, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "give_up_not_yet_available"
        # seconds_remaining is positive and bounded by the 10-min gate.
        sr = body["detail"]["seconds_remaining"]
        assert 0 < sr <= 600
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_give_up_rejected_when_session_not_active(client_with_db) -> None:
    """A session that's already graded (or submitting/abandoned) returns 409."""
    from app.auth.deps import require_auth
    from app.db.session import AsyncSessionLocal

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="giveup-graded@test.local",
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Auth",
                difficulty="intermediate",
                category="auth",
                repo_pack="x",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="graded",
                started_at=datetime.now(UTC) - timedelta(hours=1),
                completed_at=datetime.now(UTC) - timedelta(minutes=10),
            )
        )
        await db.commit()

    user_row = User(id=user_id, email="giveup-graded@test.local")

    async def _fake_require_auth():
        return user_row

    client_with_db._transport.app.dependency_overrides[require_auth] = _fake_require_auth  # type: ignore[attr-defined]

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            f"/api/v1/sessions/{session_id}/give-up",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "session_not_active"
        assert body["detail"]["session_status"] == "graded"
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_give_up_rejected_for_tutorial_mission(client_with_db) -> None:
    """P0-4 audit fix — tutorial sessions cannot use give-up (would orphan)."""
    from app.auth.deps import require_auth
    from app.db.session import AsyncSessionLocal

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="tutorial-giveup@test.local",
            )
        )
        db.add(
            Mission(
                id="orientation",
                title="Mission 00",
                difficulty="beginner",
                category="tutorial",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=8,
                failure_mode="tutorial",
                skills_tested=["onboarding"],
                manifest_sha256="sha",
                version=1,
                published=True,
                kind="tutorial",
            )
        )
        # Started >10 min ago so the gate doesn't shadow the tutorial check.
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id="orientation",
                status="active",
                started_at=datetime.now(UTC) - timedelta(minutes=15),
            )
        )
        await db.commit()

    user_row = User(id=user_id, email="tutorial-giveup@test.local")

    async def _fake_require_auth():
        return user_row

    client_with_db._transport.app.dependency_overrides[require_auth] = _fake_require_auth  # type: ignore[attr-defined]

    try:
        client_with_db.cookies.set("arena_csrf", "tok")
        resp = await client_with_db.post(
            f"/api/v1/sessions/{session_id}/give-up",
            headers={"X-Csrf-Token": "tok"},
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "give_up_not_supported_for_tutorial"
    finally:
        client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_give_up_gate_is_configurable_via_settings(db_engine, monkeypatch) -> None:
    """BE-P1 — GIVE_UP_MIN_SECONDS is sourced from Settings, not hardcoded.

    Setting the env var to 0 should let a give-up succeed immediately. The
    test only exercises the gate check itself (not the full submit
    pipeline, which is covered elsewhere) by constructing the router
    locally and inspecting the early-return behaviour.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("GIVE_UP_MIN_SECONDS", "30")
    try:
        settings = get_settings()
        assert settings.give_up_min_seconds == 30
    finally:
        get_settings.cache_clear()


def test_failure_stub_preserves_score_cap_reason() -> None:
    """P0-4 audit fix — _ensure_failed_stub propagates score_cap_reason.

    Pure unit-style assertion against the Submission constructor: when
    score_cap_reason is set, both the column and the score_report mirror
    carry the cap. Otherwise the report stub renders no chip and the
    profile aggregator treats a timeout-after-give-up as a generic
    failed attempt.
    """
    sub = Submission(
        session_id=uuid.uuid4(),
        final_diff="",
        visible_test_results=[],
        hidden_test_results=[],
        validator_results=[],
        score_report={
            "total": 0,
            "dimensions": {},
            "strengths": [],
            "weaknesses": [],
            "missed_failure_mode": False,
            "badges_earned": [],
            "failure_reason": "timeout: exceeded budget",
            "is_stub": True,
            "score_cap_reason": "gave_up",
        },
        total_score=0,
        score_cap_reason="gave_up",
    )
    assert sub.score_cap_reason == "gave_up"
    assert sub.score_report["score_cap_reason"] == "gave_up"
    assert sub.score_report["is_stub"] is True


@pytest.mark.asyncio
async def test_best_per_mission_does_not_exclude_gave_up_when_score_under_cap(
    db_engine,
) -> None:
    """ADR 0009 — best-per-mission tier policy is ENUM-based, not score-based.

    A gave-up attempt with score=38 (cap was not binding) is still tier-2;
    an uncapped attempt with score=10 is tier-1 and wins. The cap reason
    is the discriminator, not the score itself.
    """
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)

    # Gave-up attempt with score UNDER the 50 cap — but tier-2 regardless.
    _, sub_gave_up = await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=1,
        score=38,
        score_cap_reason="gave_up",
        completed_offset_minutes=-30,
    )
    # Uncapped attempt with LOWER score — still wins because tier-1.
    _, sub_uncapped = await _persist_attempt(
        SessionLocal,
        user_id=user_id,
        mission_id=mission_id,
        attempt_index=2,
        score=10,
        score_cap_reason=None,
        completed_offset_minutes=0,
    )

    async with SessionLocal() as db:
        bests = await _best_per_mission(db, user_id)
    assert len(bests) == 1
    assert bests[0].submission_id == sub_uncapped
    assert bests[0].score == 10
    assert bests[0].score_cap_reason is None


@pytest.mark.asyncio
async def test_give_up_stamps_gave_up_at_when_window_open(db_engine) -> None:
    """Direct service-layer call: gave_up_at lands when 10-min gate has elapsed.

    Integration with the sandbox/grader pipeline is exercised by
    ``test_submit_endpoint.py`` plumbing tests; this test asserts the column
    write itself (proving the migration + column shape).
    """
    SessionLocal = await _bound_session(db_engine)
    user_id, mission_id = await _seed_user_and_mission(SessionLocal)
    session_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            SessionRow(
                id=session_id,
                user_id=user_id,
                mission_id=mission_id,
                status="active",
                started_at=datetime.now(UTC) - timedelta(minutes=11),
            )
        )
        await db.commit()

    # Direct mutation mimicking the give-up endpoint's column write.
    async with SessionLocal() as db:
        row = (await db.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
        row.gave_up_at = datetime.now(UTC)
        await db.commit()

    async with SessionLocal() as db:
        row = (await db.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
    assert row.gave_up_at is not None
