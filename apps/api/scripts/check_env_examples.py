#!/usr/bin/env python3
"""Verify ``apps/api/.env.example`` carries every backend key from root.

P1-3 — the two ``.env.example`` files are coordinated by hand. Drift
between them is the regression that landed us with seven missing keys
(``GIVE_UP_MIN_SECONDS``, ``SMTP_USERNAME``, ``SMTP_PASSWORD``,
``SMTP_START_TLS``, ``SMTP_VERIFY_CERTS``, ``PROVISION_IN_PROCESS``,
``CORS_EXTRA_ORIGINS``) in the API-only example for a full release. This
script asserts the relationship so CI catches the next drift.

Definition of "backend key": any uppercase env-var name from the root
``.env.example`` whose name does NOT start with ``NEXT_PUBLIC_`` (those
are intentionally frontend-only).

Exit codes
----------
``0`` when ``keys(root) - NEXT_PUBLIC_* ⊆ keys(api)``. Non-zero with a
human-readable diff otherwise.

Usage
-----
::

    uv run python apps/api/scripts/check_env_examples.py

CI guidance
-----------
Wire as a quick pre-commit / GitHub Actions check. The script has no
runtime dependencies and finishes in milliseconds.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


_ASSIGN_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")
_FRONTEND_PREFIXES = ("NEXT_PUBLIC_",)


def _extract_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _ASSIGN_RE.match(line)
            if match:
                keys.add(match.group(1))
    return keys


def _backend_only(keys: set[str]) -> set[str]:
    return {k for k in keys if not any(k.startswith(p) for p in _FRONTEND_PREFIXES)}


def main() -> int:
    here = Path(__file__).resolve()
    api_root = here.parent.parent  # apps/api/scripts/file -> apps/api
    repo_root = api_root.parent.parent  # apps/api -> repo root

    root_env = repo_root / ".env.example"
    api_env = api_root / ".env.example"

    for label, path in (("root .env.example", root_env), ("api .env.example", api_env)):
        if not path.exists():
            print(f"check_env_examples: missing {label}: {path}", file=sys.stderr)
            return 2

    root_keys = _backend_only(_extract_keys(root_env))
    api_keys = _extract_keys(api_env)

    missing = sorted(root_keys - api_keys)
    if missing:
        print(
            "check_env_examples: apps/api/.env.example is missing backend keys "
            "declared in the root example:",
            file=sys.stderr,
        )
        for key in missing:
            print(f"  - {key}", file=sys.stderr)
        print(
            "\nAdd each key (with a safe dev default + a comment) to "
            "apps/api/.env.example so the standalone API run keeps parity.",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_env_examples: ok — {len(root_keys)} backend keys agree "
        f"between root and apps/api/.env.example"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
