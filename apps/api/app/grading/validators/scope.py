"""Diff-scope validator.

Enforces limits on file count, line count, and glob-based path rules.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult

_KIND = "diff_scope"


def _glob_matches(path: str, pattern: str) -> bool:
    """Glob match with ``**`` traversal support (``fnmatch`` doesn't handle ``**``)."""
    if "**" not in pattern:
        return fnmatch.fnmatch(path, pattern)
    regex = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    regex += "(?:.*/)?"
                    i += 3
                    continue
                regex += ".*"
                i += 2
                continue
            regex += "[^/]*"
            i += 1
            continue
        if c == "?":
            regex += "[^/]"
            i += 1
            continue
        regex += re.escape(c)
        i += 1
    return re.fullmatch(regex, path) is not None


def validate_diff_scope(diff: ParsedDiff, rule_config: dict[str, Any]) -> ValidatorResult:
    """Validate that the diff stays within declared scope limits.

    ``rule_config`` keys (all optional):
    - ``max_files_changed``: int — hard ceiling on number of changed files
    - ``max_added_lines``: int — hard ceiling on total added lines
    - ``must_touch_any_of``: list[str] — at least one of these glob patterns must
      be matched by a changed path
    - ``must_not_touch``: list[str] — none of these glob patterns may be matched
      by any changed path
    """
    violations: list[str] = []
    evidence: list[dict[str, Any]] = []

    changed = diff.changed_paths()
    added = diff.added_lines_total()

    max_files = rule_config.get("max_files_changed")
    if max_files is not None and len(changed) > max_files:
        msg = f"diff touches {len(changed)} files but max_files_changed={max_files}"
        violations.append(msg)
        evidence.append({"check": "max_files_changed", "actual": len(changed), "limit": max_files})

    max_lines = rule_config.get("max_added_lines")
    if max_lines is not None and added > max_lines:
        msg = f"diff adds {added} lines but max_added_lines={max_lines}"
        violations.append(msg)
        evidence.append({"check": "max_added_lines", "actual": added, "limit": max_lines})

    must_touch = rule_config.get("must_touch_any_of", [])
    if must_touch:
        hit = any(any(_glob_matches(p, pattern) for pattern in must_touch) for p in changed)
        if not hit:
            msg = f"diff must touch at least one of {must_touch!r} but changed only: {changed!r}"
            violations.append(msg)
            evidence.append(
                {"check": "must_touch_any_of", "patterns": must_touch, "changed_paths": changed}
            )

    must_not_touch = rule_config.get("must_not_touch", [])
    forbidden_touched: list[str] = []
    for p in changed:
        for pattern in must_not_touch:
            if _glob_matches(p, pattern):
                forbidden_touched.append(p)
                break
    if forbidden_touched:
        msg = (
            f"diff touches forbidden path(s): {forbidden_touched!r} "
            f"(must_not_touch={must_not_touch!r})"
        )
        violations.append(msg)
        evidence.append({"check": "must_not_touch", "forbidden_paths": forbidden_touched})

    return ValidatorResult(
        kind=_KIND,
        passed=len(violations) == 0,
        violations=violations,
        penalty=0,  # scope violations don't carry a point penalty (they affect dimensions)
        evidence=evidence,
    )
