"""Tests-pass validator.

Wraps test-runner output (exit codes, counts) into a ValidatorResult.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.grading.validators.base import ValidatorResult

_KIND = "tests_pass"


@dataclass
class TestRunResult:
    suite: str
    exit_code: int
    stdout: str
    stderr: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
        }


def validate_tests_pass(results: list[TestRunResult]) -> ValidatorResult:
    """Validate that all test suites exited with code 0.

    A non-zero exit code from any suite is a violation. The list of failing
    suite names is included in ``violations``.
    """
    violations: list[str] = []
    evidence: list[dict[str, Any]] = []

    for result in results:
        if result.exit_code != 0:
            msg = (
                f"suite '{result.suite}' failed with exit_code={result.exit_code} "
                f"(passed={result.passed}, failed={result.failed})"
            )
            violations.append(msg)
            evidence.append(
                {
                    "suite": result.suite,
                    "exit_code": result.exit_code,
                    "failed": result.failed,
                    "stdout_tail": result.stdout[-500:] if result.stdout else "",
                    "stderr_tail": result.stderr[-500:] if result.stderr else "",
                }
            )

    return ValidatorResult(
        kind=_KIND,
        passed=len(violations) == 0,
        violations=violations,
        penalty=0,
        evidence=evidence,
    )
