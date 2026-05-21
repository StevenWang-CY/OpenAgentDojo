"""No-new-dependencies validator.

Flags any diff that adds lines to dependency manifests (package.json,
package-lock.json, requirements*.txt, pyproject.toml).
"""

from __future__ import annotations

import fnmatch
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult

_KIND = "no_new_dependencies"

_DEP_FILE_PATTERNS: list[str] = [
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements*.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
]


def _is_dep_file(path: str) -> bool:
    filename = path.rsplit("/", maxsplit=1)[-1]
    return any(fnmatch.fnmatch(filename, pat) for pat in _DEP_FILE_PATTERNS)


def validate_no_new_deps(diff: ParsedDiff) -> ValidatorResult:
    """Validate that no dependency manifest files were modified with additions.

    Passes if none of the changed paths are dependency files, or if changed
    dependency files have zero added lines (pure removals are allowed).
    """
    violations: list[str] = []
    evidence: list[dict[str, Any]] = []

    for path in diff.changed_paths():
        if not _is_dep_file(path):
            continue
        # Count lines added to this file specifically.
        added_count = len(diff.added_lines_for_file(path))
        if added_count > 0:
            msg = f"dependency file {path!r} has {added_count} added line(s)"
            violations.append(msg)
            evidence.append(
                {
                    "file": path,
                    "added_lines": added_count,
                }
            )

    return ValidatorResult(
        kind=_KIND,
        passed=len(violations) == 0,
        violations=violations,
        penalty=0,
        evidence=evidence,
    )
