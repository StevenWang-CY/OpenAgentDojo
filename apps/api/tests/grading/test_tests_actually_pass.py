"""tests_actually_pass validator tests."""

from __future__ import annotations

from app.grading.validators import dispatch
from app.grading.validators.tests_pass import TestRunResult, validate_tests_pass


def test_all_zero_exit_passes() -> None:
    results = [
        TestRunResult(suite="unit", exit_code=0, stdout="ok", stderr=""),
        TestRunResult(suite="hidden", exit_code=0, stdout="ok", stderr=""),
    ]
    result = validate_tests_pass(results)
    assert result.passed is True
    assert result.violations == []


def test_failing_suite_violates() -> None:
    results = [
        TestRunResult(suite="unit", exit_code=0, stdout="", stderr=""),
        TestRunResult(suite="hidden", exit_code=1, stdout="", stderr="boom", failed=2),
    ]
    result = validate_tests_pass(results)
    assert result.passed is False
    assert any("hidden" in v for v in result.violations)


def test_dispatch_tests_actually_pass() -> None:
    rule = {"kind": "tests_actually_pass"}
    ctx = {
        "test_results": [
            TestRunResult(suite="unit", exit_code=0, stdout="", stderr=""),
        ],
    }
    result = dispatch(rule, ctx)
    assert result.kind == "tests_pass"
    assert result.passed is True
