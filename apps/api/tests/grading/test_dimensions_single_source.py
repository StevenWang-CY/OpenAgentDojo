"""Assert the rubric dimensions are sourced from a single table.

Three call sites used to keep their own copy of the seven rubric dimensions:

* ``apps/api/app/grading/runner.py`` (with max scores)
* ``apps/api/app/profiles/router.py`` (names only)
* ``apps/api/app/grading/score.py`` (literal dict in ``compute_score``)

Each duplicate drifted independently — a dimension renamed in the runner
silently dropped out of the profile radar. This test pins them all to
:mod:`app.grading.dimensions` so the drift is structurally impossible.
"""

from __future__ import annotations

from app.grading.dimensions import (
    DIMENSION_MAX,
    DIMENSION_NAMES,
    RUBRIC_DIMENSIONS,
    RUBRIC_TOTAL,
)


def test_runner_uses_central_rubric() -> None:
    from app.grading import runner as runner_mod

    # The runner module re-exports / re-uses the dimensions tuple.
    assert tuple(runner_mod._RUBRIC_DIMENSIONS) == RUBRIC_DIMENSIONS


def test_profile_router_uses_central_rubric() -> None:
    from app.profiles import router as profile_router

    assert tuple(profile_router._RUBRIC_DIMENSIONS) == DIMENSION_NAMES


def test_score_engine_emits_exactly_the_seven_dimensions() -> None:
    """compute_score must populate exactly the canonical seven keys."""
    from app.grading.diff import ParsedDiff
    from app.grading.score import compute_score

    report = compute_score(
        diff=ParsedDiff(""),
        events=[],
        validator_results=[],
        test_results=[],
        manifest=object(),
        agent_turns=[],
    )

    assert tuple(report.dimensions.keys()) == DIMENSION_NAMES
    for name, max_score in RUBRIC_DIMENSIONS:
        assert report.dimensions[name].max_score == max_score, (
            f"dimension {name!r} max={report.dimensions[name].max_score} != central {max_score}"
        )


def test_total_is_100() -> None:
    """The rubric must sum to 100 — anything else breaks the FE radar scale."""
    assert RUBRIC_TOTAL == 100
    assert sum(DIMENSION_MAX.values()) == 100
