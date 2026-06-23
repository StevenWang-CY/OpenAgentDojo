"""P2 — the ``no_validation_removed`` validator must be reachable from a manifest.

The validator is implemented and registered in
``app.grading.validators`` (``VALIDATORS["no_validation_removed"]``), but the
manifest ``Validator`` union historically did not admit its ``kind``, so no
mission could declare it — the registered validator was dead code. These tests
pin the union admission (parse side) and confirm a manifest-declared rule still
trips the real validator through the dispatch registry (grade side).
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from app.grading.diff import ParsedDiff
from app.grading.validators import dispatch
from app.missions.manifest import Validator, ValidatorNoValidationRemoved

_VALIDATOR_ADAPTER: TypeAdapter[object] = TypeAdapter(Validator)


def test_manifest_admits_no_validation_removed_bare() -> None:
    """A bare ``{kind: no_validation_removed}`` entry parses to its variant."""
    parsed = _VALIDATOR_ADAPTER.validate_python({"kind": "no_validation_removed"})
    assert isinstance(parsed, ValidatorNoValidationRemoved)
    assert parsed.patterns == []


def test_manifest_admits_no_validation_removed_with_patterns() -> None:
    """The optional ``patterns`` field round-trips through the union."""
    parsed = _VALIDATOR_ADAPTER.validate_python(
        {"kind": "no_validation_removed", "patterns": [r"customGuard"]}
    )
    assert isinstance(parsed, ValidatorNoValidationRemoved)
    assert parsed.patterns == [r"customGuard"]


def test_manifest_rejects_unknown_field_on_variant() -> None:
    """``extra=forbid`` keeps the variant tight (no silent typo'd keys)."""
    with pytest.raises(ValidationError):
        _VALIDATOR_ADAPTER.validate_python(
            {"kind": "no_validation_removed", "patters": ["typo"]}
        )


def test_declared_rule_trips_validator_on_removed_guard() -> None:
    """A manifest-shaped rule routed through ``dispatch`` flags a removed guard.

    This is the end-to-end contract: parse the rule the way the loader would,
    then hand it to the registry dispatcher with a diff that deletes a guard
    clause. The registered ``no_validation_removed`` validator must fail.
    """
    rule = _VALIDATOR_ADAPTER.validate_python({"kind": "no_validation_removed"})
    diff = ParsedDiff(
        "--- a/src/route.ts\n+++ b/src/route.ts\n"
        "@@ -1,3 +1,1 @@\n const x = 1;\n-requireAuth(req);\n-const y = 2;\n"
    )
    result = dispatch(rule, {"diff": diff})
    assert result.kind == "no_validation_removed"
    assert result.passed is False
    assert any("requireauth" in v.lower() for v in result.violations)


def test_declared_rule_passes_when_no_guard_removed() -> None:
    """A benign diff (pure addition) leaves the declared rule passing."""
    rule = _VALIDATOR_ADAPTER.validate_python({"kind": "no_validation_removed"})
    diff = ParsedDiff(
        "--- a/src/route.ts\n+++ b/src/route.ts\n"
        "@@ -1,1 +1,2 @@\n const x = 1;\n+const y = 2;\n"
    )
    result = dispatch(rule, {"diff": diff})
    assert result.passed is True


def test_declared_custom_pattern_is_honoured_through_dispatch() -> None:
    """A manifest-declared custom pattern widens detection via the registry."""
    rule = _VALIDATOR_ADAPTER.validate_python(
        {"kind": "no_validation_removed", "patterns": [r"customGuard"]}
    )
    diff = ParsedDiff(
        "--- a/src/route.ts\n+++ b/src/route.ts\n"
        "@@ -1,2 +1,1 @@\n const x = 1;\n-customGuard();\n"
    )
    result = dispatch(rule, {"diff": diff})
    assert result.passed is False
    assert any("customguard" in v.lower() for v in result.violations)
