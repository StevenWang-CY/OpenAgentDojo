"""regression_test_required validator tests."""

from __future__ import annotations

from app.grading.diff import ParsedDiff
from app.grading.validators.regression_test import validate_regression_test_required


def _diff_with_test(body: str) -> str:
    return (
        "--- a/backend/src/tests/auth.test.ts\n"
        "+++ b/backend/src/tests/auth.test.ts\n"
        "@@ -1,1 +1,2 @@\n"
        " const x = 1;\n"
        f"+{body}\n"
    )


def test_no_test_file_fails() -> None:
    diff = (
        "--- a/src/index.ts\n+++ b/src/index.ts\n@@ -1,1 +1,2 @@\n a\n+b\n"
    )
    parsed = ParsedDiff(diff)
    result = validate_regression_test_required(
        parsed,
        test_globs=["backend/src/tests/**/*.test.ts"],
        keywords_any_of=["expired"],
    )
    assert result.passed is False


def test_test_file_without_keyword_fails() -> None:
    parsed = ParsedDiff(_diff_with_test("it('something', () => {});"))
    result = validate_regression_test_required(
        parsed,
        test_globs=["backend/src/tests/**/*.test.ts"],
        keywords_any_of=["expired", "expiration"],
    )
    assert result.passed is False


def test_test_file_with_keyword_passes() -> None:
    parsed = ParsedDiff(
        _diff_with_test("it('rejects expired cookies', () => {});")
    )
    result = validate_regression_test_required(
        parsed,
        test_globs=["backend/src/tests/**/*.test.ts"],
        keywords_any_of=["expired", "expiration"],
    )
    assert result.passed is True
    assert any(
        isinstance(ev, dict) and ev.get("hit_keyword") == "expired"
        for ev in result.evidence
    )
