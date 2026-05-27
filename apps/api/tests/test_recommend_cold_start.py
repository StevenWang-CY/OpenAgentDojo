"""P1-2 — cold-start ladder.

A user with zero graded submissions must receive the canonical
introductory ladder (missions 01, 02, 03) with the cold-start
diagnosis copy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.recommendations.copy import COLD_START_DIAGNOSIS
from app.recommendations.engine import (
    INTRODUCTORY_LADDER,
    MissionCandidate,
    UserHistory,
    recommend,
)


def _ladder_catalogue() -> list[MissionCandidate]:
    return [
        MissionCandidate(
            mission_id=mid,
            title=f"Mission {mid}",
            language="typescript",
            difficulty="beginner",
            kind="standard",
            expected_weak_dim="safety",
            tags=(),
        )
        for mid in INTRODUCTORY_LADDER
    ] + [
        # An unrelated mission that must NOT appear in the cold-start path.
        MissionCandidate(
            mission_id="excessive-rewrite",
            title="Excessive Rewrite",
            language="typescript",
            difficulty="advanced",
            kind="standard",
            expected_weak_dim="safety",
            tags=("excessive_rewrite",),
        ),
    ]


def test_cold_start_returns_introductory_ladder() -> None:
    history = UserHistory()  # zero graded submissions
    result = recommend(
        user_history=history,
        mission_catalogue=_ladder_catalogue(),
        now=datetime(2026, 5, 27, tzinfo=UTC),
    )
    assert result.weakest_dim is None
    assert result.diagnosis == COLD_START_DIAGNOSIS
    assert [r.mission_id for r in result.recommendations] == list(INTRODUCTORY_LADDER)


def test_cold_start_with_partial_catalogue_does_not_crash() -> None:
    # Test env may ship only a subset of the canonical missions; the
    # ladder degrades gracefully without raising.
    history = UserHistory()
    truncated = [
        MissionCandidate(
            mission_id="auth-cookie-expiration",
            title="Auth Cookie",
            language="typescript",
            difficulty="beginner",
            kind="standard",
            expected_weak_dim="safety",
            tags=(),
        ),
    ]
    result = recommend(
        user_history=history,
        mission_catalogue=truncated,
        now=datetime(2026, 5, 27, tzinfo=UTC),
    )
    assert len(result.recommendations) == 1
    assert result.recommendations[0].mission_id == "auth-cookie-expiration"
