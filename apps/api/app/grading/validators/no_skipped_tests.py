"""No-skipped-tests validator.

Detects skip markers (e.g. ``.skip(``, ``xit(``) in the added lines of a diff.
"""

from __future__ import annotations

from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult

_KIND = "no_skipped_tests"


def validate_no_skipped_tests(
    diff: ParsedDiff,
    patterns: list[str],
) -> ValidatorResult:
    """Validate that no test-skip markers were introduced in the diff.

    Checks every *added* line (lines beginning with ``+``) across all files
    in the diff. Any line that contains one of the ``patterns`` is a violation.
    """
    violations: list[str] = []
    evidence: list[dict[str, Any]] = []

    added_lines = diff.all_added_lines()

    for line in added_lines:
        for pattern in patterns:
            if pattern in line:
                msg = f"added line contains skip marker {pattern!r}: {line.rstrip()!r}"
                violations.append(msg)
                evidence.append(
                    {
                        "pattern": pattern,
                        "line": line.rstrip(),
                    }
                )
                # Report each matching line once (don't double-count on
                # multiple patterns per line).
                break

    return ValidatorResult(
        kind=_KIND,
        passed=len(violations) == 0,
        violations=violations,
        penalty=0,
        evidence=evidence,
    )
