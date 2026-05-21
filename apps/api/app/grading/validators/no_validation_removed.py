"""No-validation-removed validator.

Detects removal of auth/validation guard clauses from the diff.
"""

from __future__ import annotations

import re
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult

_KIND = "no_validation_removed"

# Default guard-clause patterns to watch for in *removed* lines.
_DEFAULT_PATTERNS: list[str] = [
    r"if\s*\(!",  # if (!something)
    r"\bassert\b",  # assert ...
    r"\bauthorize\b",  # authorize(
    r"\bauthenticate\b",  # authenticate(
    r"\bverify\b",  # verify(
    r"\bvalidate\b",  # validate(
    r"\brequireAuth\b",  # requireAuth
    r"\bcheckPermission\b",  # checkPermission(
    r"\bguard\b",  # guard(
    r"\bensureAuth\b",  # ensureAuth
    r"\bprotected\b",  # protected route/method
    r"\bacl\b",  # ACL check
    r"401|403",  # explicit status codes for auth errors
]


def validate_no_validation_removed(
    diff: ParsedDiff,
    patterns: list[str] | None = None,
) -> ValidatorResult:
    """Validate that no auth or validation guard clauses were removed.

    Examines *removed* lines (lines starting with ``-``) in the diff. Each
    removed line is checked against ``patterns`` (or the built-in defaults if
    none provided). A violation is raised for every removed guard clause.
    """
    check_patterns = list(patterns or []) + _DEFAULT_PATTERNS
    compiled = [re.compile(p, re.IGNORECASE) for p in check_patterns]

    violations: list[str] = []
    evidence: list[dict[str, Any]] = []

    for line in diff.all_removed_lines():
        line_stripped = line.rstrip()
        for pat, raw in zip(compiled, check_patterns, strict=True):
            if pat.search(line_stripped):
                msg = f"removed line matches validation guard pattern /{raw}/: {line_stripped!r}"
                violations.append(msg)
                evidence.append(
                    {
                        "pattern": raw,
                        "removed_line": line_stripped[:200],
                    }
                )
                break  # Only report once per line.

    return ValidatorResult(
        kind=_KIND,
        passed=len(violations) == 0,
        violations=violations,
        penalty=0,
        evidence=evidence,
    )
