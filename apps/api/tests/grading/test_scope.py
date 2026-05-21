"""diff_scope validator tests."""

from __future__ import annotations

from app.grading.diff import ParsedDiff
from app.grading.validators import dispatch
from app.grading.validators.scope import validate_diff_scope

_DIFF_TWO_FILES = (
    "--- a/backend/src/auth/session.ts\n+++ b/backend/src/auth/session.ts\n"
    "@@ -1,1 +1,2 @@\n const x = 1;\n+const y = 2;\n"
    "--- a/frontend/src/LoginForm.tsx\n+++ b/frontend/src/LoginForm.tsx\n"
    "@@ -1,1 +1,2 @@\n const a = 1;\n+const b = 2;\n"
)


def test_max_files_changed_breach() -> None:
    parsed = ParsedDiff(_DIFF_TWO_FILES)
    result = validate_diff_scope(parsed, {"max_files_changed": 1})
    assert result.passed is False
    assert any("2 files" in v for v in result.violations)


def test_max_added_lines_breach() -> None:
    parsed = ParsedDiff(_DIFF_TWO_FILES)
    result = validate_diff_scope(parsed, {"max_added_lines": 1})
    assert result.passed is False
    assert any("2 lines" in v for v in result.violations)


def test_must_touch_satisfied() -> None:
    parsed = ParsedDiff(_DIFF_TWO_FILES)
    result = validate_diff_scope(
        parsed,
        {"must_touch_any_of": ["backend/src/auth/session.ts"]},
    )
    assert result.passed is True


def test_must_not_touch_glob_breach() -> None:
    parsed = ParsedDiff(_DIFF_TWO_FILES)
    result = validate_diff_scope(parsed, {"must_not_touch": ["frontend/**"]})
    assert result.passed is False
    assert any("frontend/src/LoginForm.tsx" in v for v in result.violations)


def test_dispatch_routes_diff_scope() -> None:
    parsed = ParsedDiff(_DIFF_TWO_FILES)
    rule = {"kind": "diff_scope", "max_files_changed": 5}
    result = dispatch(rule, {"diff": parsed})
    assert result.kind == "diff_scope"
    assert result.passed is True
