"""P1-2 — engine determinism contract.

Given the same ``(user_history, mission_catalogue, RUBRIC_VERSION)``,
:func:`app.recommendations.engine.recommend` must produce byte-identical
output across replays. This test runs the engine twice on the same
fixture and asserts the JSON-serialised payloads are equal — including
the diagnosis copy and per-mission "why" strings.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.recommendations.engine import (
    MissionCandidate,
    UserHistory,
    _BestAttempt,
    recommend,
)


def _fixture_catalogue() -> list[MissionCandidate]:
    return [
        MissionCandidate(
            mission_id="auth-cookie-expiration",
            title="Auth Cookie Expiration",
            language="typescript",
            difficulty="intermediate",
            kind="standard",
            expected_weak_dim="safety",
            tags=("checks_presence_not_expiration", "skill:auth"),
        ),
        MissionCandidate(
            mission_id="agent-wrong-file",
            title="Agent Wrong File",
            language="typescript",
            difficulty="intermediate",
            kind="standard",
            expected_weak_dim="safety",
            tags=("wrong_layer_committed",),
        ),
        MissionCandidate(
            mission_id="missing-regression-test",
            title="Missing Regression Test",
            language="typescript",
            difficulty="beginner",
            kind="standard",
            expected_weak_dim="safety",
            tags=("missing_regression_test",),
        ),
        MissionCandidate(
            mission_id="excessive-rewrite",
            title="Excessive Rewrite",
            language="typescript",
            difficulty="advanced",
            kind="standard",
            expected_weak_dim="safety",
            tags=("excessive_rewrite",),
        ),
        MissionCandidate(
            mission_id="goroutine-leak",
            title="Goroutine Leak",
            language="go",
            difficulty="advanced",
            kind="standard",
            expected_weak_dim="safety",
            tags=("goroutine_leak", "skill:concurrency"),
        ),
    ]


def _fixture_history() -> UserHistory:
    # User has graded one mission with a low ``agent_review`` score so
    # the engine picks ``agent_review`` as the weakest dim. The other
    # dims sit high enough that the argmin tie-break engages on rubric
    # order only when ratios are equal — see the dedicated tie-break test.
    bests = {
        "auth-cookie-expiration": _BestAttempt(
            mission_id="auth-cookie-expiration",
            score=55,
            dimensions={
                "final_correctness": 25,
                "verification": 12,
                "agent_review": 4,  # 4/15 ≈ 0.27 → weakest
                "prompt_quality": 8,
                "context_selection": 8,
                "safety": 9,
                "diff_minimality": 9,
            },
            graded_at=datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC),
        ),
    }
    return UserHistory(
        best_attempts=bests,
        per_mission_attempt_count={"auth-cookie-expiration": 1},
    )


def test_recommend_is_byte_deterministic() -> None:
    catalogue = _fixture_catalogue()
    history = _fixture_history()
    pinned = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)

    first = recommend(
        user_history=history,
        mission_catalogue=catalogue,
        now=pinned,
    )
    second = recommend(
        user_history=history,
        mission_catalogue=catalogue,
        now=pinned,
    )

    # Pydantic JSON dump must be byte-identical — covers diagnosis copy
    # and per-mission ``why`` strings (P1-2 spec §Testing).
    assert first.model_dump_json() == second.model_dump_json()


def test_recommend_includes_diagnosis_and_three_items() -> None:
    catalogue = _fixture_catalogue()
    history = _fixture_history()
    result = recommend(
        user_history=history,
        mission_catalogue=catalogue,
        now=datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
    )
    assert result.weakest_dim == "agent_review"
    assert len(result.recommendations) == 3
    assert "agent" in result.diagnosis.lower()
