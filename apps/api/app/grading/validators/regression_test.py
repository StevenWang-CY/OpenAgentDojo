"""Regression-test required validator.

Checks that the diff includes a test file (matching test_globs) that also
contains at least one of the supplied keywords.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult

_KIND = "regression_test_required"


def _glob_matches(path: str, pattern: str) -> bool:
    """Glob match with proper ``**`` handling.

    ``fnmatch.fnmatch`` treats ``**`` as a single ``*``, so it never matches
    across ``/`` boundaries. We translate the pattern into a regex where
    ``**`` becomes ``.*``.
    """
    if "**" not in pattern:
        return fnmatch.fnmatch(path, pattern)
    # Build a regex: escape, then convert globs.
    # 1) `**/`  -> `(?:.*/)?` (matches zero or more directories)
    # 2) `**`   -> `.*`
    # 3) `*`    -> `[^/]*`
    # 4) `?`    -> `[^/]`
    regex = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # **/  -> (?:.*/)?
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    regex += "(?:.*/)?"
                    i += 3
                    continue
                # ** at end / mid -> .*
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


def validate_regression_test_required(
    diff: ParsedDiff,
    test_globs: list[str],
    keywords_any_of: list[str],
) -> ValidatorResult:
    """Validate that a regression test was added.

    Passes when:
    1. At least one changed file matches one of ``test_globs``.
    2. The diff text for that file contains at least one keyword from
       ``keywords_any_of`` (case-insensitive).
    """
    violations: list[str] = []
    evidence: list[dict[str, Any]] = []

    changed_paths = diff.changed_paths()

    # Step 1 — find test files in the diff.
    matching_test_files: list[str] = []
    for path in changed_paths:
        for glob in test_globs:
            if _glob_matches(path, glob):
                matching_test_files.append(path)
                break

    if not matching_test_files:
        violations.append(
            f"no changed file matches test_globs={test_globs!r}; a regression test file is required"
        )
        evidence.append(
            {
                "check": "test_file_present",
                "test_globs": test_globs,
                "changed_paths": changed_paths,
            }
        )
        return ValidatorResult(
            kind=_KIND,
            passed=False,
            violations=violations,
            penalty=0,
            evidence=evidence,
        )

    # Step 2 — at least one keyword must appear in those test files.
    keywords_lower = [k.lower() for k in keywords_any_of]
    keyword_hit = False
    hit_file: str | None = None
    hit_keyword: str | None = None

    for path in matching_test_files:
        file_diff_text = diff.diff_text_for_file(path).lower()
        for kw in keywords_lower:
            if kw in file_diff_text:
                keyword_hit = True
                hit_file = path
                hit_keyword = kw
                break
        if keyword_hit:
            break

    if not keyword_hit:
        violations.append(
            f"test file(s) {matching_test_files!r} were changed but none "
            f"contain any of the required keywords: {keywords_any_of!r}"
        )
        evidence.append(
            {
                "check": "keyword_present",
                "test_files": matching_test_files,
                "keywords_any_of": keywords_any_of,
            }
        )
    else:
        evidence.append(
            {
                "check": "keyword_present",
                "hit_file": hit_file,
                "hit_keyword": hit_keyword,
            }
        )

    return ValidatorResult(
        kind=_KIND,
        passed=len(violations) == 0,
        violations=violations,
        penalty=0,
        evidence=evidence,
    )
