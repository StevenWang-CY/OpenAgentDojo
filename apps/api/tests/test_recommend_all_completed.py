"""P1-2 — every-mission-graded edge case.

When a user has graded every shipped mission at or above the pass
threshold, the engine returns the largest-gap-to-ideal mission as a
retry recommendation plus up to two ``coming_soon`` placeholders pulled
from the roadmap.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.recommendations.engine import (
    PASS_THRESHOLD,
    MissionCandidate,
    UserHistory,
    _BestAttempt,
    _PlaceholderCandidate,
    recommend,
)


def _catalogue() -> list[MissionCandidate]:
    return [
        MissionCandidate(
            mission_id="auth-cookie-expiration",
            title="Auth Cookie Expiration",
            language="typescript",
            difficulty="intermediate",
            kind="standard",
            expected_weak_dim="safety",
            tags=(),
        ),
        MissionCandidate(
            mission_id="agent-wrong-file",
            title="Agent Wrong File",
            language="typescript",
            difficulty="intermediate",
            kind="standard",
            expected_weak_dim="safety",
            tags=(),
        ),
    ]


def _all_passed_history() -> UserHistory:
    return UserHistory(
        best_attempts={
            "auth-cookie-expiration": _BestAttempt(
                mission_id="auth-cookie-expiration",
                score=PASS_THRESHOLD + 5,
                dimensions={
                    "final_correctness": 28,
                    "verification": 13,
                    "agent_review": 13,
                    "prompt_quality": 9,
                    "context_selection": 9,
                    "safety": 9,
                    "diff_minimality": 9,
                },
                graded_at=datetime(2026, 5, 20, tzinfo=UTC),
            ),
            "agent-wrong-file": _BestAttempt(
                mission_id="agent-wrong-file",
                score=PASS_THRESHOLD + 15,
                dimensions={
                    "final_correctness": 29,
                    "verification": 14,
                    "agent_review": 14,
                    "prompt_quality": 9,
                    "context_selection": 9,
                    "safety": 9,
                    "diff_minimality": 9,
                },
                graded_at=datetime(2026, 5, 21, tzinfo=UTC),
            ),
        },
        per_mission_attempt_count={
            "auth-cookie-expiration": 1,
            "agent-wrong-file": 1,
        },
    )


def test_all_graded_returns_largest_gap_plus_coming_soon() -> None:
    coming_soon = [
        _PlaceholderCandidate(
            mission_id="react-shop-frontend",
            title="React shop",
            language="typescript",
            target_release_date="2026-06-15",
        ),
        _PlaceholderCandidate(
            mission_id="go-context-cancel-dropped",
            title="Go context",
            language="go",
            target_release_date="2026-07-15",
        ),
    ]
    result = recommend(
        user_history=_all_passed_history(),
        mission_catalogue=_catalogue(),
        coming_soon=coming_soon,
        now=datetime(2026, 5, 27, tzinfo=UTC),
    )

    # Three items: one shipped retry + two coming_soon placeholders.
    assert len(result.recommendations) == 3
    statuses = [r.status for r in result.recommendations]
    assert statuses == ["shipped", "coming_soon", "coming_soon"]

    # Largest-gap retry: auth-cookie-expiration (score PASS+5) wins over
    # agent-wrong-file (score PASS+15).
    assert result.recommendations[0].mission_id == "auth-cookie-expiration"

    # Coming soon placeholders carry their release date intact.
    assert result.recommendations[1].mission_id == "react-shop-frontend"
    assert result.recommendations[1].target_release_date == "2026-06-15"
    assert result.recommendations[2].mission_id == "go-context-cancel-dropped"
