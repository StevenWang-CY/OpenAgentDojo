"""no_new_dependencies validator tests."""

from __future__ import annotations

from app.grading.diff import ParsedDiff
from app.grading.validators.deps import validate_no_new_deps


def test_package_json_addition_trips() -> None:
    diff = "--- a/package.json\n+++ b/package.json\n@@ -1,1 +1,2 @@\n line\n+left-pad: 1.0.0\n"
    parsed = ParsedDiff(diff)
    result = validate_no_new_deps(parsed)
    assert result.passed is False


def test_lockfile_addition_trips() -> None:
    diff = (
        "--- a/pnpm-lock.yaml\n+++ b/pnpm-lock.yaml\n@@ -1,1 +1,2 @@\n "
        "version: 1\n+left-pad: 1.0.0\n"
    )
    parsed = ParsedDiff(diff)
    result = validate_no_new_deps(parsed)
    assert result.passed is False


def test_non_dep_file_passes() -> None:
    diff = "--- a/src/index.ts\n+++ b/src/index.ts\n@@ -1,1 +1,2 @@\n a\n+b\n"
    parsed = ParsedDiff(diff)
    result = validate_no_new_deps(parsed)
    assert result.passed is True
