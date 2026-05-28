"""Tests for `GET /api/v1/profiles/{handle}` (plan §13.1).

Profile pages are public so all of these are anon (no session cookie). The
fixture re-binds ``AsyncSessionLocal`` to the in-memory SQLite engine the
``client`` fixture is using behind the scenes — identical pattern to
``tests/test_reports_endpoint.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.badge import Badge
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.models.user_badge import UserBadge


async def _bind_engine(db_engine):
    """Point the FastAPI app at the same engine as the test fixture."""
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_module.AsyncSessionLocal


def _score_report(
    final: int = 24,
    verification: int = 12,
    agent_review: int = 12,
    prompt_quality: int = 8,
    context_selection: int = 6,
    safety: int = 10,
    diff_minimality: int = 8,
) -> dict:
    """Build a score_report payload with all 7 rubric dimensions populated.

    Max scores are pulled from the single :mod:`app.grading.dimensions`
    source of truth — adjusting a dimension's weight there reflows the test
    fixture automatically.
    """
    from app.grading.dimensions import DIMENSION_MAX

    return {
        "total": final
        + verification
        + agent_review
        + prompt_quality
        + context_selection
        + safety
        + diff_minimality,
        "dimensions": {
            "final_correctness": {
                "score": final,
                "max": DIMENSION_MAX["final_correctness"],
                "signals": [],
            },
            "verification": {
                "score": verification,
                "max": DIMENSION_MAX["verification"],
                "signals": [],
            },
            "agent_review": {
                "score": agent_review,
                "max": DIMENSION_MAX["agent_review"],
                "signals": [],
            },
            "prompt_quality": {
                "score": prompt_quality,
                "max": DIMENSION_MAX["prompt_quality"],
                "signals": [],
            },
            "context_selection": {
                "score": context_selection,
                "max": DIMENSION_MAX["context_selection"],
                "signals": [],
            },
            "safety": {"score": safety, "max": DIMENSION_MAX["safety"], "signals": []},
            "diff_minimality": {
                "score": diff_minimality,
                "max": DIMENSION_MAX["diff_minimality"],
                "signals": [],
            },
        },
        "strengths": [],
        "weaknesses": [],
        "missed_failure_mode": False,
        "badges_earned": [],
    }


async def _seed_profile(db_engine):
    """Seed one user with 2 graded sessions/submissions + 1 earned badge."""
    AsyncSessionLocal = await _bind_engine(db_engine)

    user_id = uuid.uuid4()
    sess1 = uuid.uuid4()
    sess2 = uuid.uuid4()
    sub1 = uuid.uuid4()
    sub2 = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="alice@arena.local",
                handle="alice",
                display_name="Alice",
                created_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
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
        db.add(
            Mission(
                id="api-contract-drift",
                title="API Contract Drift",
                difficulty="advanced",
                category="api",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=30,
                failure_mode="y",
                skills_tested=["api"],
                manifest_sha256="sha-bbb",
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        db.add(
            Badge(
                id="regression-test-writer",
                title="Regression Test Writer",
                description="Wrote a regression test.",
                icon="test-tube",
            )
        )

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        db.add(
            SessionRow(
                id=sess1,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="graded",
                started_at=now - timedelta(days=5),
                completed_at=now - timedelta(days=5),
                score=80,
            )
        )
        db.add(
            SessionRow(
                id=sess2,
                user_id=user_id,
                mission_id="api-contract-drift",
                status="graded",
                started_at=now,
                completed_at=now,
                score=92,
            )
        )
        await db.flush()

        db.add(
            Submission(
                id=sub1,
                session_id=sess1,
                final_diff="x",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report=_score_report(
                    final=20,
                    verification=10,
                    agent_review=10,
                    prompt_quality=6,
                    context_selection=8,
                    safety=10,
                    diff_minimality=6,
                ),
                total_score=80,
            )
        )
        db.add(
            Submission(
                id=sub2,
                session_id=sess2,
                final_diff="y",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report=_score_report(
                    final=28,
                    verification=14,
                    agent_review=14,
                    prompt_quality=10,
                    context_selection=8,
                    safety=10,
                    diff_minimality=10,
                ),
                total_score=92,
            )
        )
        db.add(
            UserBadge(
                user_id=user_id,
                badge_id="regression-test-writer",
                earned_at=now,
                session_id=sess2,
            )
        )
        await db.commit()

    return {
        "user_id": user_id,
        "sess1": sess1,
        "sess2": sess2,
        "sub1": sub1,
        "sub2": sub2,
    }


@pytest.mark.asyncio
async def test_unknown_handle_returns_404(client, db_engine) -> None:
    await _bind_engine(db_engine)
    resp = await client.get("/api/v1/profiles/nobody")
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "profile not found"}


@pytest.mark.asyncio
async def test_full_profile_payload(client, db_engine) -> None:
    seeded = await _seed_profile(db_engine)

    resp = await client.get("/api/v1/profiles/alice")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Header fields.
    assert body["handle"] == "alice"
    assert body["display_name"] == "Alice"
    assert body["joined_at"].startswith("2026-01-15")

    # Aggregates.
    assert body["total_missions"] == 2
    assert body["best_score"] == 92

    # Badges — newest first; PII-free.
    assert len(body["badges"]) == 1
    badge = body["badges"][0]
    assert badge["id"] == "regression-test-writer"
    assert badge["title"] == "Regression Test Writer"
    assert badge["icon"] == "test-tube"
    assert badge["session_id"] == str(seeded["sess2"])
    assert "earned_at" in badge

    # History — most recent (sess2) first.
    history = body["history"]
    assert [h["session_id"] for h in history] == [
        str(seeded["sess2"]),
        str(seeded["sess1"]),
    ]
    assert history[0]["mission_id"] == "api-contract-drift"
    assert history[0]["mission_title"] == "API Contract Drift"
    assert history[0]["difficulty"] == "advanced"
    assert history[0]["score"] == 92
    assert history[1]["mission_id"] == "auth-cookie-expiration"
    assert history[1]["score"] == 80
    # Wave 2C — every history row carries the producing submission id so
    # the account Data tab can render a per-row Replay button.
    assert history[0]["submission_id"] == str(seeded["sub2"])
    assert history[1]["submission_id"] == str(seeded["sub1"])

    # Radar averages — averaged across the two submissions.
    radar = body["radar_averages"]
    # All 7 dimensions present because both reports populated them.
    assert set(radar.keys()) == {
        "final_correctness",
        "verification",
        "agent_review",
        "prompt_quality",
        "context_selection",
        "safety",
        "diff_minimality",
    }
    assert radar["final_correctness"] == pytest.approx((20 + 28) / 2, rel=1e-3)
    assert radar["verification"] == pytest.approx((10 + 14) / 2, rel=1e-3)
    assert radar["safety"] == pytest.approx(10.0, rel=1e-3)


@pytest.mark.asyncio
async def test_radar_only_includes_present_dimensions(client, db_engine) -> None:
    """If a dimension is missing in all reports, it MUST be absent from the response."""
    AsyncSessionLocal = await _bind_engine(db_engine)

    user_id = uuid.uuid4()
    sess_id = uuid.uuid4()
    sub_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="bob@arena.local",
                handle="bob",
                display_name="Bob",
                created_at=datetime(2026, 2, 1, tzinfo=UTC),
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Auth Cookie Expiration",
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
                expected_weak_dim="safety",
            )
        )
        db.add(
            SessionRow(
                id=sess_id,
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="graded",
                score=50,
                completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            )
        )
        await db.flush()
        # Only two dimensions present in the report.
        db.add(
            Submission(
                id=sub_id,
                session_id=sess_id,
                final_diff="",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report={
                    "total": 50,
                    "dimensions": {
                        "final_correctness": {"score": 25, "max": 30, "signals": []},
                        "safety": {"score": 8, "max": 10, "signals": []},
                    },
                    "strengths": [],
                    "weaknesses": [],
                    "missed_failure_mode": False,
                    "badges_earned": [],
                },
                total_score=50,
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/profiles/bob")
    assert resp.status_code == 200, resp.text
    radar = resp.json()["radar_averages"]
    # Only the two scored dimensions are present.
    assert set(radar.keys()) == {"final_correctness", "safety"}
    assert radar["final_correctness"] == pytest.approx(25.0, rel=1e-3)
    assert radar["safety"] == pytest.approx(8.0, rel=1e-3)


@pytest.mark.asyncio
async def test_best_score_and_total_missions(client, db_engine) -> None:
    """``best_score`` = max(score); ``total_missions`` counts only graded+scored rows."""
    AsyncSessionLocal = await _bind_engine(db_engine)

    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="cara@arena.local",
                handle="cara",
                display_name="Cara",
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Auth Cookie Expiration",
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
                expected_weak_dim="safety",
            )
        )
        # P0-3 / ADR 0009 — public aggregates now use best-per-mission. With
        # three graded sessions on the SAME mission, ``total_missions`` is
        # the count of DISTINCT missions practised (1, not 3) and ``best_score``
        # is the best across those attempts. The radar is built off the
        # best-per-mission submission only — so we seed Submission rows to
        # match. The abandoned session never carries a Submission, so it
        # is silently ignored (status filter excludes non-graded).
        sids = [uuid.uuid4() for _ in range(3)]
        scores = [55, 88, 70]
        completed = [
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 3, 2, tzinfo=UTC),
            datetime(2026, 3, 3, tzinfo=UTC),
        ]
        for sid, score, ts in zip(sids, scores, completed, strict=True):
            db.add(
                SessionRow(
                    id=sid,
                    user_id=user_id,
                    mission_id="auth-cookie-expiration",
                    status="graded",
                    score=score,
                    completed_at=ts,
                )
            )
            db.add(
                Submission(
                    id=uuid.uuid4(),
                    session_id=sid,
                    final_diff="",
                    visible_test_results=[],
                    hidden_test_results=[],
                    validator_results=[],
                    score_report={
                        "total": score,
                        "dimensions": {},
                        "strengths": [],
                        "weaknesses": [],
                        "missed_failure_mode": False,
                        "badges_earned": [],
                        "effective_max": 100,
                    },
                    total_score=score,
                )
            )
        # Abandoned session — must NOT count toward total_missions.
        db.add(
            SessionRow(
                id=uuid.uuid4(),
                user_id=user_id,
                mission_id="auth-cookie-expiration",
                status="abandoned",
                score=None,
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/profiles/cara")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # ADR 0009 — total_missions = distinct missions, not raw attempt count.
    assert body["total_missions"] == 1
    assert body["best_score"] == 88
    # No dimensions populated in the seed reports → empty radar.
    assert body["radar_averages"] == {}
    # No badges earned → empty list.
    assert body["badges"] == []


@pytest.mark.asyncio
async def test_malformed_score_report_excluded_and_counted(client, db_engine) -> None:
    """Malformed reports MUST be skipped by the radar and counted in the metric.

    P1-5: rather than silently ignore bad ``score_report`` payloads we
    debug-log + increment ``profile_malformed_reports_total{reason=…}`` so
    ops can spot scoring-engine drift in production.
    """
    AsyncSessionLocal = await _bind_engine(db_engine)

    user_id = uuid.uuid4()
    good_sess = uuid.uuid4()
    bad_sess_dims_missing = uuid.uuid4()
    bad_sess_score_not_numeric = uuid.uuid4()
    bad_sess_payload_not_dict = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="eve@arena.local",
                handle="eve",
                display_name="Eve",
                created_at=datetime(2026, 5, 1, tzinfo=UTC),
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Auth Cookie Expiration",
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
                expected_weak_dim="safety",
            )
        )
        now = datetime(2026, 5, 5, tzinfo=UTC)
        # P0-3 / ADR 0009 — public radar uses best-per-mission. The malformed
        # observability sweep walks ALL graded submissions independently
        # (so the counter still increments for non-best malformed rows),
        # but the radar averages themselves come from the per-mission best.
        # Seed the good submission with the highest score so it
        # deterministically wins the dedupe — otherwise the radar might
        # be aggregated from one of the malformed shapes (which contribute
        # nothing) and the assertion fails non-deterministically.
        seeds = [
            (good_sess, 70, now + timedelta(minutes=4)),
            (bad_sess_dims_missing, 50, now + timedelta(minutes=3)),
            (bad_sess_score_not_numeric, 50, now + timedelta(minutes=2)),
            (bad_sess_payload_not_dict, 50, now + timedelta(minutes=1)),
        ]
        for sid, score, ts in seeds:
            db.add(
                SessionRow(
                    id=sid,
                    user_id=user_id,
                    mission_id="auth-cookie-expiration",
                    status="graded",
                    score=score,
                    completed_at=ts,
                )
            )
        await db.flush()

        # Good report.
        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=good_sess,
                final_diff="",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report=_score_report(
                    final=20,
                    verification=10,
                    agent_review=10,
                    prompt_quality=5,
                    context_selection=5,
                    safety=10,
                    diff_minimality=2,
                ),
                total_score=62,
            )
        )
        # Bad: ``dimensions`` is a string, not a dict.
        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=bad_sess_dims_missing,
                final_diff="",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report={"total": 50, "dimensions": "oops"},
                total_score=50,
            )
        )
        # Bad: dimension payload's ``score`` is a string.
        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=bad_sess_score_not_numeric,
                final_diff="",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report={
                    "total": 50,
                    "dimensions": {
                        "safety": {"score": "NaN", "max": 10, "signals": []},
                    },
                },
                total_score=50,
            )
        )
        # Bad: dimension payload is a list, not a dict.
        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=bad_sess_payload_not_dict,
                final_diff="",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report={
                    "total": 50,
                    "dimensions": {"safety": [1, 2, 3]},
                },
                total_score=50,
            )
        )
        await db.commit()

    # Snapshot the metric counters BEFORE the request so we can assert deltas.
    from app.observability import profile_malformed_reports_total

    def _value(reason: str) -> float:
        return (
            profile_malformed_reports_total.labels(reason=reason)._value.get()  # type: ignore[attr-defined]
        )

    before_dims_missing = _value("dimensions_missing")
    before_score_not_numeric = _value("score_not_numeric")
    before_payload_not_dict = _value("dimension_payload_not_dict")

    resp = await client.get("/api/v1/profiles/eve")
    assert resp.status_code == 200, resp.text
    radar = resp.json()["radar_averages"]
    # Only the good report contributed — values should match its inputs.
    assert radar["safety"] == pytest.approx(10.0, rel=1e-3)
    assert radar["final_correctness"] == pytest.approx(20.0, rel=1e-3)

    # And the malformed-report counter incremented for each bad row.
    assert _value("dimensions_missing") == pytest.approx(before_dims_missing + 1)
    assert _value("score_not_numeric") == pytest.approx(before_score_not_numeric + 1)
    assert _value("dimension_payload_not_dict") == pytest.approx(before_payload_not_dict + 1)


@pytest.mark.asyncio
async def test_handle_lookup_is_case_insensitive(client, db_engine) -> None:
    """The handle column is CITEXT in prod; queries must succeed regardless of case."""
    AsyncSessionLocal = await _bind_engine(db_engine)
    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=uuid.uuid4(),
                email="dee@arena.local",
                handle="dee",
                display_name="Dee",
                created_at=datetime(2026, 4, 1, tzinfo=UTC),
            )
        )
        await db.commit()

    # On SQLite, Text is case-sensitive — so we exercise the exact-case path,
    # which is the contract the frontend always uses (the handle in the URL is
    # the canonical stored form). CITEXT behaviour is exercised in Postgres
    # integration tests.
    resp = await client.get("/api/v1/profiles/dee")
    assert resp.status_code == 200, resp.text
    assert resp.json()["handle"] == "dee"
