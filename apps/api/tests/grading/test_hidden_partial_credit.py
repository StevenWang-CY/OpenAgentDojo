"""Hidden-test credit is proportional, not binary (P0-3).

The previous rule awarded +12 only when *every* hidden test passed and
floored Final Correctness at 18 otherwise. A supervisor who fixed 9 of 10
root-cause behaviours scored identically to one who fixed 0. This wasted
signal and gave the user no granular feedback.

The new rule:
  * hidden_credit = round(12 * passed / total)
  * when any hidden test fails, the dimension is capped at
    18 (non-hidden ceiling) + hidden_credit.

Skipped tests do not count toward the total — they assert nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.score import _score_final_correctness, compute_score
from app.grading.validators.tests_pass import TestRunResult


@dataclass
class _HiddenTests:
    suites: list[str] = field(default_factory=list)


@dataclass
class _Repo:
    test_commands: dict[str, str] = field(default_factory=dict)


@dataclass
class _Manifest:
    id: str = "hidden-partial-credit-test"
    repo: _Repo = field(default_factory=_Repo)
    hidden_tests: _HiddenTests = field(
        default_factory=lambda: _HiddenTests(suites=["hidden"])
    )
    expected_files: list[str] = field(default_factory=list)
    expected_diff_lines_p50: int = 20
    expected_context: object = None
    reward_signals: object = None


def _diff_touching(path: str) -> ParsedDiff:
    return ParsedDiff(
        f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,2 @@\n const x = 1;\n+const y = 2;\n"
    )


def test_full_hidden_pass_awards_full_credit() -> None:
    manifest = _Manifest(expected_files=["src/foo.ts"])
    test_results = [
        TestRunResult(suite="hidden", exit_code=0, stdout="", stderr="", passed=10),
    ]
    ds = _score_final_correctness(
        diff=_diff_touching("src/foo.ts"),
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
    )
    # 12 hidden + 8 visible-N/A + 6 regression + 4 root-cause = 30.
    assert ds.score == 30, f"expected 30, got {ds.score}; signals={ds.signals}"


def test_nine_of_ten_hidden_awards_proportional_credit() -> None:
    """The headline P0-3 case: 90% pass rate must score meaningfully higher
    than the historical 18-point floor."""
    manifest = _Manifest(expected_files=["src/foo.ts"])
    test_results = [
        TestRunResult(suite="hidden", exit_code=1, stdout="", stderr="", passed=9, failed=1),
    ]
    ds = _score_final_correctness(
        diff=_diff_touching("src/foo.ts"),
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
    )
    # round(12 * 9/10) = 11 hidden credit.
    # ceiling = 18 + 11 = 29 (since one hidden failed).
    # raw = 11 hidden + 8 visible-N/A + 6 regression + 4 root-cause = 29 ≤ ceiling.
    assert ds.score == 29, f"expected 29, got {ds.score}; signals={ds.signals}"


def test_zero_of_ten_hidden_matches_old_floor() -> None:
    """Backwards-compat: a total hidden failure still caps at 18."""
    manifest = _Manifest(expected_files=["src/foo.ts"])
    test_results = [
        TestRunResult(suite="hidden", exit_code=1, stdout="", stderr="", passed=0, failed=10),
    ]
    ds = _score_final_correctness(
        diff=_diff_touching("src/foo.ts"),
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
    )
    # hidden_credit = 0, ceiling = 18, raw = 0+8+6+4 = 18.
    assert ds.score == 18, f"expected 18, got {ds.score}; signals={ds.signals}"


def test_half_pass_lands_between_floor_and_ceiling() -> None:
    manifest = _Manifest(expected_files=["src/foo.ts"])
    test_results = [
        TestRunResult(suite="hidden", exit_code=1, stdout="", stderr="", passed=5, failed=5),
    ]
    ds = _score_final_correctness(
        diff=_diff_touching("src/foo.ts"),
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
    )
    # round(12 * 0.5) = 6 hidden credit.
    # ceiling = 24, raw = 6+8+6+4 = 24.
    assert ds.score == 24, f"expected 24, got {ds.score}; signals={ds.signals}"


def test_skipped_tests_excluded_from_total() -> None:
    """Skipped tests are not assertions and must not dilute the pass rate."""
    manifest = _Manifest(expected_files=["src/foo.ts"])
    test_results = [
        TestRunResult(
            suite="hidden",
            exit_code=0,
            stdout="",
            stderr="",
            passed=8,
            failed=0,
            skipped=2,
        ),
    ]
    ds = _score_final_correctness(
        diff=_diff_touching("src/foo.ts"),
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
    )
    # 8/8 passed → full 12 hidden credit, full 30.
    assert ds.score == 30, f"expected 30, got {ds.score}; signals={ds.signals}"


def test_multiple_hidden_suites_aggregate() -> None:
    manifest = _Manifest(
        expected_files=["src/foo.ts"],
        hidden_tests=_HiddenTests(suites=["hidden-auth", "hidden-perms"]),
    )
    test_results = [
        TestRunResult(suite="hidden-auth", exit_code=0, stdout="", stderr="", passed=4),
        TestRunResult(suite="hidden-perms", exit_code=1, stdout="", stderr="", passed=2, failed=2),
    ]
    ds = _score_final_correctness(
        diff=_diff_touching("src/foo.ts"),
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
    )
    # 6 of 8 hidden passed → round(12 * 0.75) = 9.
    # ceiling = 18 + 9 = 27, raw = 9+8+6+4 = 27.
    assert ds.score == 27, f"expected 27, got {ds.score}; signals={ds.signals}"


def test_per_suite_breakdown_surfaced_in_signals() -> None:
    """Users need to know *which* hidden suite failed to learn from it."""
    manifest = _Manifest(
        expected_files=["src/foo.ts"],
        hidden_tests=_HiddenTests(suites=["hidden-auth", "hidden-perms"]),
    )
    test_results = [
        TestRunResult(suite="hidden-auth", exit_code=0, stdout="", stderr="", passed=4),
        TestRunResult(suite="hidden-perms", exit_code=1, stdout="", stderr="", passed=0, failed=3),
    ]
    ds = _score_final_correctness(
        diff=_diff_touching("src/foo.ts"),
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
    )
    joined = "\n".join(ds.signals)
    assert "hidden suite hidden-auth: 4/4 passed" in joined
    assert "hidden suite hidden-perms: 0/3 passed" in joined


def test_full_pipeline_partial_credit_preserves_replay_determinism() -> None:
    """Replays of the same inputs produce the same score."""
    manifest = _Manifest(expected_files=["src/foo.ts"])
    test_results = [
        TestRunResult(suite="hidden", exit_code=1, stdout="", stderr="", passed=7, failed=3),
    ]
    diff = _diff_touching("src/foo.ts")
    first = compute_score(
        diff=diff,
        events=[],
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
        agent_turns=[],
    )
    second = compute_score(
        diff=diff,
        events=[],
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
        agent_turns=[],
    )
    assert first.dimensions["final_correctness"].score == second.dimensions[
        "final_correctness"
    ].score
