"""Runner-side bucketing + score-side credit must agree on hidden suite names.

Historically the runner used an exact lowercase set lookup against
``manifest.hidden_tests.suites`` while ``score.py`` did
``"hidden" in r.suite.lower()`` — so a manifest-declared hidden suite called
``"e2e-canary"`` bucketed as hidden in the runner but was invisible to the
correctness gate. The new ``is_hidden_suite`` predicate is the single source
of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.grading.diff import ParsedDiff
from app.grading.runner import _split_results, is_hidden_suite
from app.grading.score import _hidden_tests_passed, compute_score
from app.grading.validators.tests_pass import TestRunResult


@dataclass
class _HiddenTests:
    suites: list[str] = field(default_factory=list)


@dataclass
class _Repo:
    test_commands: dict[str, str] = field(default_factory=dict)


@dataclass
class _Manifest:
    id: str = "hidden-suite-consistency"
    repo: _Repo = field(default_factory=_Repo)
    hidden_tests: _HiddenTests = field(default_factory=_HiddenTests)
    expected_files: list[str] = field(default_factory=list)
    expected_diff_lines_p50: int = 20
    expected_context: object = None
    reward_signals: object = None


def test_is_hidden_suite_recognises_manifest_declared_suite() -> None:
    manifest = _Manifest(hidden_tests=_HiddenTests(suites=["e2e-canary"]))
    assert is_hidden_suite(manifest, "e2e-canary")
    assert is_hidden_suite(manifest, "E2E-Canary")  # case insensitive
    assert not is_hidden_suite(manifest, "unit")


def test_runner_split_buckets_named_hidden_suite() -> None:
    manifest = _Manifest(hidden_tests=_HiddenTests(suites=["e2e-canary"]))
    results = {
        "unit": TestRunResult(suite="unit", exit_code=0, stdout="", stderr=""),
        "e2e-canary": TestRunResult(suite="e2e-canary", exit_code=0, stdout="", stderr=""),
    }
    visible, hidden = _split_results(results, manifest)
    assert {r.suite for r in hidden} == {"e2e-canary"}
    assert {r.suite for r in visible} == {"unit"}


def test_score_hidden_pass_credits_named_hidden_suite() -> None:
    """The correctness gate must see the same hidden suite the runner did."""
    manifest = _Manifest(hidden_tests=_HiddenTests(suites=["e2e-canary"]))
    passing = [
        TestRunResult(suite="e2e-canary", exit_code=0, stdout="", stderr="", passed=4),
    ]
    assert _hidden_tests_passed(passing, manifest) is True

    failing = [
        TestRunResult(
            suite="e2e-canary", exit_code=1, stdout="", stderr="", failed=2
        ),
    ]
    assert _hidden_tests_passed(failing, manifest) is False


def test_full_pipeline_credits_named_hidden_suite() -> None:
    """End-to-end: compute_score sees the suite as hidden and awards correctness."""
    manifest = _Manifest(
        hidden_tests=_HiddenTests(suites=["e2e-canary"]),
        repo=_Repo(test_commands={}),
        expected_files=["src/foo.ts"],
    )
    diff = (
        "--- a/src/foo.ts\n"
        "+++ b/src/foo.ts\n"
        "@@ -1,1 +1,2 @@\n"
        " const x = 1;\n"
        "+const y = 2;\n"
    )
    report = compute_score(
        diff=ParsedDiff(diff),
        events=[],
        validator_results=[],
        test_results=[
            TestRunResult(
                suite="e2e-canary", exit_code=0, stdout="", stderr="", passed=4
            )
        ],
        manifest=manifest,
        agent_turns=[],
    )
    correctness = report.dimensions["final_correctness"]
    # 12 (hidden pass) + 8 (visible N/A) + 6 (regression default) + 4 (root cause) = 30.
    assert correctness.score == 30, (
        f"named hidden suite + root-cause should hit 30; got "
        f"{correctness.score}; signals={correctness.signals}"
    )
