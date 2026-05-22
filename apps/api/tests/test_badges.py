"""Badge-award rule tests (plan §11.4).

Pure-function ``compute_badges`` exercises each rule in isolation. The DB
persistence path (``award``) is covered by the submit endpoint round-trip.
"""

from __future__ import annotations

from typing import Any

from app.grading.badges import (
    AGENT_SKEPTIC,
    API_CONTRACT_GUARDIAN,
    CONCURRENCY_DEBUGGER,
    MINIMAL_DIFF,
    REGRESSION_WRITER,
    SECURITY_AWARE,
    compute_badges,
)
from app.grading.score import DimensionScore, ScoreReport
from app.grading.validators.base import ValidatorResult
from app.grading.validators.tests_pass import TestRunResult


def _score(
    diff_min: int = 0,
    correctness: int = 0,
) -> ScoreReport:
    return ScoreReport(
        total=0,
        dimensions={
            "final_correctness": DimensionScore(score=correctness, max_score=30),
            "verification": DimensionScore(score=0, max_score=20),
            "agent_review": DimensionScore(score=0, max_score=15),
            "prompt_quality": DimensionScore(score=0, max_score=10),
            "context_selection": DimensionScore(score=0, max_score=10),
            "safety": DimensionScore(score=0, max_score=10),
            "diff_minimality": DimensionScore(score=diff_min, max_score=5),
        },
        strengths=[],
        weaknesses=[],
        missed_failure_mode=False,
        badges_earned=[],
    )


def test_regression_writer_awarded_when_validator_passes() -> None:
    vr = ValidatorResult(
        kind="regression_test_required",
        passed=True,
        evidence=[{"hit_keyword": "expired"}],
    )
    out = compute_badges(
        score_report=_score(),
        validator_results=[vr],
        test_results=[],
        events=[],
        mission_id="auth-cookie-expiration",
    )
    assert REGRESSION_WRITER in out


def test_regression_writer_keyword_must_align_with_failure_mode() -> None:
    """Keyword mismatched with the failure mode should not award the badge."""

    class _FM:
        id = "checks_presence_not_expiration"
        title = "Agent validates cookie existence but not expiration"
        description = "expiration matters"

    class _Manifest:
        failure_mode = _FM()

    vr = ValidatorResult(
        kind="regression_test_required",
        passed=True,
        evidence=[{"hit_keyword": "ttl"}],
    )
    out = compute_badges(
        score_report=_score(),
        validator_results=[vr],
        test_results=[],
        events=[],
        mission_id="auth-cookie-expiration",
        manifest=_Manifest(),
    )
    # "ttl" is not in the failure_mode words; should NOT award.
    assert REGRESSION_WRITER not in out

    vr_ok = ValidatorResult(
        kind="regression_test_required",
        passed=True,
        evidence=[{"hit_keyword": "expiration"}],
    )
    out_ok = compute_badges(
        score_report=_score(),
        validator_results=[vr_ok],
        test_results=[],
        events=[],
        mission_id="auth-cookie-expiration",
        manifest=_Manifest(),
    )
    assert REGRESSION_WRITER in out_ok


def test_security_aware_requires_forbidden_pass_and_revert_event() -> None:
    forbidden_ok = ValidatorResult(kind="forbidden_changes", passed=True)
    events: list[dict[str, Any]] = [
        {"event_type": "patch.applied", "payload": {}},
        {"event_type": "file.reverted", "payload": {"path": "x"}},
    ]
    out = compute_badges(
        score_report=_score(),
        validator_results=[forbidden_ok],
        test_results=[],
        events=events,
        mission_id="x",
    )
    assert SECURITY_AWARE in out

    # Without a revert event → no badge.
    out_no_revert = compute_badges(
        score_report=_score(),
        validator_results=[forbidden_ok],
        test_results=[],
        events=[{"event_type": "patch.applied", "payload": {}}],
        mission_id="x",
    )
    assert SECURITY_AWARE not in out_no_revert


def test_agent_skeptic_requires_corrective_prompt_diff_and_edit() -> None:
    events = [
        {"event_type": "patch.applied", "payload": {}},
        {"event_type": "diff.opened", "payload": {"path": "x"}},
        {
            "event_type": "prompt.submitted",
            "payload": {"text": "revise this", "intent": "revise"},
        },
        {"event_type": "file.edited", "payload": {"path": "x"}},
    ]
    out = compute_badges(
        score_report=_score(),
        validator_results=[],
        test_results=[],
        events=events,
        mission_id="x",
    )
    assert AGENT_SKEPTIC in out

    # Missing file.edited → no badge.
    events_missing = events[:3]
    out2 = compute_badges(
        score_report=_score(),
        validator_results=[],
        test_results=[],
        events=events_missing,
        mission_id="x",
    )
    assert AGENT_SKEPTIC not in out2


def test_minimal_diff_requires_full_minimality_and_correctness() -> None:
    out = compute_badges(
        score_report=_score(diff_min=5, correctness=24),
        validator_results=[],
        test_results=[],
        events=[],
        mission_id="x",
    )
    assert MINIMAL_DIFF in out

    out_low = compute_badges(
        score_report=_score(diff_min=5, correctness=20),
        validator_results=[],
        test_results=[],
        events=[],
        mission_id="x",
    )
    assert MINIMAL_DIFF not in out_low


def test_concurrency_debugger_only_for_async_race_mission() -> None:
    hidden = TestRunResult(suite="hidden", exit_code=0, stdout="", stderr="", passed=4, failed=0)
    out = compute_badges(
        score_report=_score(),
        validator_results=[],
        test_results=[hidden],
        events=[],
        mission_id="async-race-condition",
    )
    assert CONCURRENCY_DEBUGGER in out

    # Wrong mission id → no badge.
    out_other = compute_badges(
        score_report=_score(),
        validator_results=[],
        test_results=[hidden],
        events=[],
        mission_id="auth-cookie-expiration",
    )
    assert CONCURRENCY_DEBUGGER not in out_other


def test_api_contract_guardian_only_for_api_contract_drift() -> None:
    rgr = ValidatorResult(kind="regression_test_required", passed=True)
    out = compute_badges(
        score_report=_score(),
        validator_results=[rgr],
        test_results=[],
        events=[],
        mission_id="api-contract-drift",
    )
    assert API_CONTRACT_GUARDIAN in out

    out_other = compute_badges(
        score_report=_score(),
        validator_results=[rgr],
        test_results=[],
        events=[],
        mission_id="x",
    )
    assert API_CONTRACT_GUARDIAN not in out_other
