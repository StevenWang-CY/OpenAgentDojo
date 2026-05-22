"""no_secrets_exposed validator tests."""

from __future__ import annotations

from app.grading.diff import ParsedDiff
from app.grading.validators.secrets import validate_no_secrets


def test_quoted_secret_assignment_trips() -> None:
    diff = (
        "--- a/src/config.ts\n+++ b/src/config.ts\n"
        '@@ -1,1 +1,2 @@\n const a = 1;\n+const AWS_SECRET = "AKIAIOSFODNN7EXAMPLE";\n'
    )
    parsed = ParsedDiff(diff)
    result = validate_no_secrets(parsed)
    assert result.passed is False
    assert any("AWS_SECRET" in v for v in result.violations)


def test_env_var_reference_passes() -> None:
    diff = (
        "--- a/src/config.ts\n+++ b/src/config.ts\n"
        "@@ -1,1 +1,2 @@\n const a = 1;\n+const SECRET = process.env.SECRET;\n"
    )
    parsed = ParsedDiff(diff)
    result = validate_no_secrets(parsed)
    assert result.passed is True


def test_placeholder_passes() -> None:
    diff = "--- a/.env.example\n+++ b/.env.example\n@@ -1,1 +1,2 @@\n FOO=bar\n+API_KEY=changeme\n"
    parsed = ParsedDiff(diff)
    result = validate_no_secrets(parsed)
    assert result.passed is True
