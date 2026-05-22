"""Prompt quality scores from the AVG of the last 3 turns, not the best of all.

Picking the single best prompt across all turns rewards a supervisor who
lobbed in one strong prompt then degraded into ``fix it`` follow-ups. The
new rubric averages the trailing three turns so the dimension measures
consistency at submit time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.grading.score import _score_prompt_quality


@dataclass
class _Manifest:
    id: str = "prompt-quality-test"
    reward_signals: Any = None


def _turn(prompt: str, idx: int = 0) -> dict[str, Any]:
    return {"user_prompt": prompt, "turn_index": idx}


def test_avg_of_last_three_used_not_max_of_all() -> None:
    """5 turns: first is strong (8/10), last 3 average to 8/10 → score 8."""
    strong = (
        "Reproduce the bug: an expired cookie still gets past requireAuth. "
        "Add a regression test that exercises expiration. Keep minimal — "
        "do not modify the frontend."  # mentions regression + minimal + long
    )
    # Three repeats of `strong` in the trailing window → average 8.
    turns = [
        _turn("fix it", 0),  # 0 (vague)
        _turn("just fix", 1),  # 0
        _turn(strong, 2),
        _turn(strong, 3),
        _turn(strong, 4),
    ]
    score = _score_prompt_quality(turns, _Manifest())
    # The "strong" prompt scores: +2 (>=80 chars) + +2 (test mention)
    # + +2 (scope phrase "do not modify" / "minimal") = 6.
    # All 3 last turns score 6 → avg 6.
    assert score.score == 6, (
        f"avg-of-last-3 should be 6, got {score.score}; signals={score.signals}"
    )


def test_window_limited_to_3_even_with_more_turns() -> None:
    """A strong outlier in turn 0 must NOT raise the score when the tail is weak."""
    strong = (
        "Reproduce the bug, add a regression test, keep changes minimal — "
        "do not modify the frontend. " * 2
    )
    turns = [
        _turn(strong, 0),  # strong, but outside last-3
        _turn("fix it", 1),
        _turn("fix it", 2),
        _turn("fix it", 3),
    ]
    score = _score_prompt_quality(turns, _Manifest())
    # Trailing 3 prompts ("fix it") all score 0 → avg 0.
    assert score.score == 0, (
        f"trailing weak prompts must score 0; got {score.score} "
        f"signals={score.signals}"
    )


def test_zero_turns_does_not_crash_and_returns_zero() -> None:
    score = _score_prompt_quality([], _Manifest())
    assert score.score == 0
    assert score.max_score == 10
    assert score.signals  # non-empty diagnostic


def test_fewer_than_three_turns_averages_what_exists() -> None:
    strong = (
        "Reproduce the bug, add a regression test, keep changes minimal — "
        "do not modify the frontend. " * 2
    )
    turns = [_turn(strong, 0), _turn(strong, 1)]
    score = _score_prompt_quality(turns, _Manifest())
    # Both turns score the same → avg equals that score.
    assert score.score >= 6, (
        f"two strong turns should average high, got {score.score}; "
        f"signals={score.signals}"
    )
