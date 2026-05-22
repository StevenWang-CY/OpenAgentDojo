"""Visible-tests gate: differentiate N/A from "configured but failed".

The previous heuristic returned ``False`` whether the manifest declared no
visible suites or had a configured suite that exited non-zero. That meant a
hidden-only mission could only ever score 24/30 on correctness even on a
perfect submission. The fix is to look at ``manifest.repo.test_commands`` —
empty → N/A → award the credit; populated → apply the existing pass/fail
logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.score import compute_score
from app.grading.validators.tests_pass import TestRunResult


@dataclass
class _Repo:
    test_commands: dict[str, str] = field(default_factory=dict)


@dataclass
class _Manifest:
    id: str = "hidden-only"
    repo: _Repo = field(default_factory=_Repo)
    expected_files: list[str] = field(default_factory=list)
    expected_diff_lines_p50: int = 20
    expected_context: Any = None
    reward_signals: Any = None
    hidden_tests: Any = None


def _passing_hidden() -> list[TestRunResult]:
    return [TestRunResult(suite="hidden", exit_code=0, stdout="", stderr="", passed=4)]


def test_no_visible_suites_treated_as_na_and_correctness_full_credit() -> None:
    manifest = _Manifest(repo=_Repo(test_commands={}))
    report = compute_score(
        diff=ParsedDiff(""),
        events=[],
        validator_results=[],
        test_results=_passing_hidden(),
        manifest=manifest,
        agent_turns=[],
    )
    correctness = report.dimensions["final_correctness"]
    # 12 (hidden pass) + 8 (visible N/A → credited) + 6 (regression ok by
    # default in N/A case) + 4 (root cause — root cause is not addressed
    # without expected_files match, so 0 here) = 26 with no root cause hit.
    assert correctness.score >= 26, (
        f"hidden-only mission scored {correctness.score}/{correctness.max_score} "
        f"signals={correctness.signals}"
    )
    assert any("N/A" in s for s in correctness.signals), correctness.signals


def test_visible_suites_configured_but_none_passed_does_not_award_credit() -> None:
    manifest = _Manifest(repo=_Repo(test_commands={"unit": "pnpm test"}))
    # Visible suite exists but failed.
    failing = TestRunResult(suite="unit", exit_code=1, stdout="", stderr="", passed=0, failed=5)
    report = compute_score(
        diff=ParsedDiff(""),
        events=[],
        validator_results=[],
        test_results=[*_passing_hidden(), failing],
        manifest=manifest,
        agent_turns=[],
    )
    correctness = report.dimensions["final_correctness"]
    # Visible failed → no +8 visible credit, no +6 regression credit.
    assert not any("N/A" in s for s in correctness.signals), correctness.signals
    assert any("visible tests did not pass" in s for s in correctness.signals), (
        correctness.signals
    )


def test_full_hidden_only_correctness_can_hit_30() -> None:
    """Hidden-only mission with expected_files hit can score the full 30/30."""
    manifest = _Manifest(
        repo=_Repo(test_commands={}),
        expected_files=["src/auth.ts"],
    )
    # A diff that touches src/auth.ts so the root-cause heuristic fires.
    diff_text = (
        "--- a/src/auth.ts\n"
        "+++ b/src/auth.ts\n"
        "@@ -1,1 +1,2 @@\n"
        " const x = 1;\n"
        "+const y = 2;\n"
    )
    report = compute_score(
        diff=ParsedDiff(diff_text),
        events=[],
        validator_results=[],
        test_results=_passing_hidden(),
        manifest=manifest,
        agent_turns=[],
    )
    correctness = report.dimensions["final_correctness"]
    assert correctness.score == 30, (
        f"hidden-only + root cause should score 30/30, got "
        f"{correctness.score}; signals={correctness.signals}"
    )
