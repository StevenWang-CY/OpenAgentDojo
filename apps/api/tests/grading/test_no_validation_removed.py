"""no_validation_removed validator tests."""

from __future__ import annotations

from app.grading.diff import ParsedDiff
from app.grading.validators.no_validation_removed import validate_no_validation_removed


def test_authorize_removal_trips() -> None:
    diff = (
        "--- a/src/route.ts\n+++ b/src/route.ts\n"
        "@@ -1,3 +1,1 @@\n const x = 1;\n-authorize(req);\n-const y = 2;\n"
    )
    parsed = ParsedDiff(diff)
    result = validate_no_validation_removed(parsed)
    assert result.passed is False
    assert any("authorize" in v.lower() for v in result.violations)


def test_no_guard_removed_passes() -> None:
    diff = "--- a/src/route.ts\n+++ b/src/route.ts\n@@ -1,1 +1,2 @@\n const x = 1;\n+const y = 2;\n"
    parsed = ParsedDiff(diff)
    result = validate_no_validation_removed(parsed)
    assert result.passed is True


def test_custom_pattern_supported() -> None:
    diff = (
        "--- a/src/route.ts\n+++ b/src/route.ts\n@@ -1,2 +1,1 @@\n const x = 1;\n-customGuard();\n"
    )
    parsed = ParsedDiff(diff)
    result = validate_no_validation_removed(parsed, [r"customGuard"])
    assert result.passed is False
