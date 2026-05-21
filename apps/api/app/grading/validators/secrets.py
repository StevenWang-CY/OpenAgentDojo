"""No-secrets-exposed validator.

Scans the diff for patterns that look like hard-coded secrets in added lines.
"""

from __future__ import annotations

import re
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult

_KIND = "no_secrets_exposed"

# Match common secret-bearing variable names followed by = and a value that
# looks like a secret (quoted string, bare token, or base64-like value).
# The value part must be > 8 chars and NOT look like a plain variable reference
# (i.e. not purely ${VAR} or a short constant like "true"/"false"/"null").
_SECRET_PATTERN = re.compile(
    r"""
    (?:^|[^\w])                         # word boundary / start
    (?P<key>
        (?:PRIVATE[_\-]?KEY|SECRET|PASSWORD|PASSWD|PWD|
           API[_\-]?KEY|API[_\-]?SECRET|
           AUTH[_\-]?TOKEN|ACCESS[_\-]?TOKEN|BEARER[_\-]?TOKEN|
           TOKEN|BEARER|
           AWS[_\-]?SECRET|AWS[_\-]?ACCESS|
           PRIVATE|CREDENTIALS|CREDENTIAL)
    )
    \s*[:=]\s*                           # assignment operator
    (?P<value>
        (?:['"]{1}[^'"]{8,}['"]{1})      # quoted string ≥ 8 chars
        |(?:[A-Za-z0-9+/\-_]{12,}={0,2})  # base64-ish or token ≥ 12 chars
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Additional patterns for things like `Bearer <token>` in headers.
_BEARER_HEADER_PATTERN = re.compile(
    r"""
    (?:Authorization|auth)\s*[:=]\s*
    ['"]{0,1}Bearer\s+(?P<token>[A-Za-z0-9._\-+/]{12,})['"]{0,1}
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Allowlist patterns — false-positive suppressors.
_SAFE_VALUES = re.compile(
    r"""
    ^(?:
        \$\{[^}]+\}                  # ${ENV_VAR}
        | \$[A-Z_]+                  # $ENV_VAR
        | process\.env\.[A-Z_]+      # process.env.X
        | os\.environ                # os.environ
        | getenv\(                   # getenv(
        | <[^>]+>                    # <placeholder>
        | \*+                        # ****
        | your[_\-]?(?:secret|key|token|password)  # placeholder wording
        | test|true|false|null|undefined|none|example|placeholder|changeme|dummy|fake
    )$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _looks_safe(value: str) -> bool:
    """Return True if the value is clearly a placeholder / env var reference."""
    stripped = value.strip("'\"\t ")
    if len(stripped) < 8:
        return True
    if _SAFE_VALUES.search(stripped):
        return True
    return False


def validate_no_secrets(diff: ParsedDiff) -> ValidatorResult:
    """Validate that no hard-coded secrets were introduced in added lines."""
    violations: list[str] = []
    evidence: list[dict[str, Any]] = []

    for line in diff.all_added_lines():
        line_stripped = line.rstrip()

        for match in _SECRET_PATTERN.finditer(line_stripped):
            value = match.group("value")
            if _looks_safe(value):
                continue
            key = match.group("key")
            redacted = value[:4] + "***" if len(value) > 4 else "***"
            msg = f"possible secret exposed: {key}=<{redacted}>"
            violations.append(msg)
            evidence.append(
                {
                    "pattern": "secret_assignment",
                    "key_name": key,
                    "line_preview": line_stripped[:120],
                }
            )

        for match in _BEARER_HEADER_PATTERN.finditer(line_stripped):
            token = match.group("token")
            if _looks_safe(token):
                continue
            redacted = token[:4] + "***" if len(token) > 4 else "***"
            msg = f"possible bearer token exposed: Bearer <{redacted}>"
            violations.append(msg)
            evidence.append(
                {
                    "pattern": "bearer_header",
                    "line_preview": line_stripped[:120],
                }
            )

    return ValidatorResult(
        kind=_KIND,
        passed=len(violations) == 0,
        violations=violations,
        penalty=0,
        evidence=evidence,
    )
