"""P1-2 — tie-break stability.

Two missions with identical ranking-score components must sort by
``mission_id`` ascending. This pins the deterministic post-condition the
spec calls out at §P1-2 step 4.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.recommendations.engine import (
    MissionCandidate,
    UserHistory,
    _BestAttempt,
    recommend,
)


def _two_identical_candidates() -> list[MissionCandidate]:
    # Two missions: identical difficulty, neither in FRESH_MISSION_IDS,
    # neither carrying a failure-mode tag that maps to the user's
    # weakest dim, both not yet attempted. Their score components are
    # identical (0 dim_alignment + 1 difficulty_match + 0 freshness +
    # 0.5 novelty_bonus = 1.5 apiece).
    return [
        MissionCandidate(
            mission_id="zzz-mission",
            title="Z Mission",
            language="typescript",
            difficulty="intermediate",
            kind="standard",
            expected_weak_dim="diff_minimality",
            tags=(),
        ),
        MissionCandidate(
            mission_id="aaa-mission",
            title="A Mission",
            language="typescript",
            difficulty="intermediate",
            kind="standard",
            expected_weak_dim="diff_minimality",
            tags=(),
        ),
        MissionCandidate(
            mission_id="mmm-mission",
            title="M Mission",
            language="typescript",
            difficulty="intermediate",
            kind="standard",
            expected_weak_dim="diff_minimality",
            tags=(),
        ),
    ]


def test_tie_break_orders_by_mission_id_ascending() -> None:
    # User has graded one mission very low on prompt_quality so that
    # dimension dominates the argmin. The three candidates above carry
    # ``expected_weak_dim="diff_minimality"`` so none aligns — they tie.
    history = UserHistory(
        best_attempts={
            "auth-cookie-expiration": _BestAttempt(
                mission_id="auth-cookie-expiration",
                score=60,
                dimensions={
                    "final_correctness": 25,
                    "verification": 12,
                    "agent_review": 12,
                    "prompt_quality": 1,  # 1/10 = 0.1 → weakest
                    "context_selection": 8,
                    "safety": 9,
                    "diff_minimality": 9,
                },
                graded_at=datetime(2026, 5, 20, tzinfo=UTC),
            )
        },
        per_mission_attempt_count={"auth-cookie-expiration": 1},
    )
    result = recommend(
        user_history=history,
        mission_catalogue=_two_identical_candidates(),
        now=datetime(2026, 5, 27, tzinfo=UTC),
    )
    assert [r.mission_id for r in result.recommendations] == [
        "aaa-mission",
        "mmm-mission",
        "zzz-mission",
    ]
