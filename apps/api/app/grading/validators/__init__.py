"""Grading validators — public API + dispatch registry.

The ``VALIDATORS`` mapping is keyed by ``kind`` and points at a callable that
takes ``(rule, ctx) -> ValidatorResult``. ``ctx`` is a plain dict carrying
whatever the validator needs (parsed diff, fs reader, test results, manifest
folder). The runner uses :func:`dispatch` so an unknown ``kind`` becomes a
caught error rather than a crash.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.grading.validators.base import ValidatorResult
from app.grading.validators.deps import validate_no_new_deps
from app.grading.validators.forbidden import validate_forbidden_changes
from app.grading.validators.no_skipped_tests import validate_no_skipped_tests
from app.grading.validators.no_validation_removed import validate_no_validation_removed
from app.grading.validators.regression_test import validate_regression_test_required
from app.grading.validators.scope import validate_diff_scope
from app.grading.validators.secrets import validate_no_secrets
from app.grading.validators.tests_pass import TestRunResult, validate_tests_pass

# ---------------------------------------------------------------------------
# Per-kind adapter functions: each turns a (rule, ctx) pair into a result.
# ---------------------------------------------------------------------------


def _forbidden_changes(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    diff = ctx["diff"]
    fs_reader = ctx["fs_reader"]
    manifest_folder: Path = ctx["manifest_folder"]
    rules_file_name = _rule_attr(rule, "rules_file")
    rules_path = manifest_folder / rules_file_name
    return validate_forbidden_changes(diff, fs_reader, rules_path)


def _diff_scope(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    diff = ctx["diff"]
    rule_dict = _rule_as_dict(rule, drop_keys=("kind",))
    return validate_diff_scope(diff, rule_dict)


def _regression_test_required(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    diff = ctx["diff"]
    test_globs = list(_rule_attr(rule, "test_globs") or [])
    keywords_any_of = list(_rule_attr(rule, "keywords_any_of") or [])
    return validate_regression_test_required(diff, test_globs, keywords_any_of)


def _no_skipped_tests(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    diff = ctx["diff"]
    patterns = list(_rule_attr(rule, "patterns") or [])
    return validate_no_skipped_tests(diff, patterns)


def _no_new_dependencies(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    diff = ctx["diff"]
    return validate_no_new_deps(diff)


def _no_secrets_exposed(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    diff = ctx["diff"]
    return validate_no_secrets(diff)


def _no_validation_removed(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    diff = ctx["diff"]
    patterns = _rule_attr(rule, "patterns") or None
    if patterns is not None:
        patterns = list(patterns)
    return validate_no_validation_removed(diff, patterns)


def _tests_actually_pass(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    test_results = ctx.get("test_results") or []
    return validate_tests_pass(list(test_results))


# ---------------------------------------------------------------------------
# Registry + dispatch
# ---------------------------------------------------------------------------


VALIDATORS: dict[str, Callable[[Any, dict[str, Any]], ValidatorResult]] = {
    "forbidden_changes": _forbidden_changes,
    "diff_scope": _diff_scope,
    "regression_test_required": _regression_test_required,
    "no_skipped_tests": _no_skipped_tests,
    "no_new_dependencies": _no_new_dependencies,
    "no_secrets_exposed": _no_secrets_exposed,
    "no_validation_removed": _no_validation_removed,
    "tests_actually_pass": _tests_actually_pass,
}


def dispatch(rule: Any, ctx: dict[str, Any]) -> ValidatorResult:
    """Route ``rule`` (manifest validator entry or string) to its handler.

    On unknown kind, returns a failed ``ValidatorResult`` instead of raising
    so the grading pipeline degrades gracefully.
    """
    kind = _rule_kind(rule)
    handler = VALIDATORS.get(kind)
    if handler is None:
        return ValidatorResult(
            kind=kind or "unknown",
            passed=False,
            violations=[f"unknown validator kind: {kind!r}"],
        )
    return handler(rule, ctx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule_kind(rule: Any) -> str:
    if isinstance(rule, str):
        return rule
    if isinstance(rule, dict):
        return str(rule.get("kind", "") or "")
    return str(getattr(rule, "kind", "") or "")


def _rule_attr(rule: Any, name: str) -> Any:
    if isinstance(rule, dict):
        return rule.get(name)
    return getattr(rule, name, None)


def _rule_as_dict(rule: Any, drop_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    if isinstance(rule, dict):
        data = dict(rule)
    # Pydantic v2 BaseModel exposes model_dump; fall back to vars().
    elif hasattr(rule, "model_dump"):
        data = rule.model_dump()
    else:
        data = dict(vars(rule))
    for k in drop_keys:
        data.pop(k, None)
    return data


__all__ = [
    "VALIDATORS",
    "TestRunResult",
    "ValidatorResult",
    "dispatch",
    "validate_diff_scope",
    "validate_forbidden_changes",
    "validate_no_new_deps",
    "validate_no_secrets",
    "validate_no_skipped_tests",
    "validate_no_validation_removed",
    "validate_regression_test_required",
    "validate_tests_pass",
]
