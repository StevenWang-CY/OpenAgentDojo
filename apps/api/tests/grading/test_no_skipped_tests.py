"""no_skipped_tests validator tests."""

from __future__ import annotations

from app.grading.diff import ParsedDiff
from app.grading.validators.no_skipped_tests import validate_no_skipped_tests


def test_skip_marker_added_triggers_violation() -> None:
    diff = (
        "--- a/src/x.test.ts\n+++ b/src/x.test.ts\n"
        "@@ -1,1 +1,2 @@\n const x = 1;\n+it.skip('busted', () => {});\n"
    )
    parsed = ParsedDiff(diff)
    result = validate_no_skipped_tests(parsed, [".skip(", "xit("])
    assert result.passed is False
    assert any(".skip(" in v for v in result.violations)


def test_clean_diff_passes() -> None:
    diff = (
        "--- a/src/x.test.ts\n+++ b/src/x.test.ts\n"
        "@@ -1,1 +1,2 @@\n const x = 1;\n+it('works', () => {});\n"
    )
    parsed = ParsedDiff(diff)
    result = validate_no_skipped_tests(parsed, [".skip(", "xit("])
    assert result.passed is True


def test_marker_in_removed_line_does_not_trip() -> None:
    diff = (
        "--- a/src/x.test.ts\n+++ b/src/x.test.ts\n"
        "@@ -1,2 +1,1 @@\n const x = 1;\n-it.skip('was bad', () => {});\n"
    )
    parsed = ParsedDiff(diff)
    result = validate_no_skipped_tests(parsed, [".skip("])
    assert result.passed is True
