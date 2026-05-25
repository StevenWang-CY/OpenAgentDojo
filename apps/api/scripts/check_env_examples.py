#!/usr/bin/env python3
"""Verify the three ``.env.example`` files stay in lockstep.

P1-3 — root, api, and compose ``.env.example`` files are coordinated by
hand. Drift between them is the regression that landed us with seven
missing keys (``GIVE_UP_MIN_SECONDS``, ``SMTP_USERNAME``,
``SMTP_PASSWORD``, ``SMTP_START_TLS``, ``SMTP_VERIFY_CERTS``,
``PROVISION_IN_PROCESS``, ``CORS_EXTRA_ORIGINS``) in the API-only
example for a full release, and then again with six missing P0-5/P0-6
keys in the compose example. This script asserts the relationship so
CI catches the next drift.

Definition of "backend key": any uppercase env-var name from the root
``.env.example`` whose name does NOT start with ``NEXT_PUBLIC_`` (those
are intentionally frontend-only and only flow into the FE bundle).

Checks performed
----------------
1. ``apps/api/.env.example`` carries every backend key from root.
2. ``infra/compose/.env.compose.example`` carries every backend key
   from root AND every ``NEXT_PUBLIC_*`` key (compose runs the FE
   container too, so the compose env must be a strict superset).

Exit codes
----------
``0`` when both relationships hold. Non-zero with a human-readable
diff otherwise.

Usage
-----
::

    uv run python apps/api/scripts/check_env_examples.py

CI guidance
-----------
Wired as a step in the ``lint-py`` GitHub Actions job and as a local
pre-commit hook. The script has no runtime dependencies and finishes
in milliseconds.
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


def _report(label: str, target: str, missing: list[str]) -> None:
    print(
        f"check_env_examples: {target} is missing {label} keys "
        "declared in the root example:",
        file=sys.stderr,
    )
    for key in missing:
        print(f"  - {key}", file=sys.stderr)
    print(
        f"\nAdd each key (with a safe dev default + a comment) to "
        f"{target} so the cross-example contract holds.",
        file=sys.stderr,
    )


def main() -> int:
    here = Path(__file__).resolve()
    api_root = here.parent.parent  # apps/api/scripts/file -> apps/api
    repo_root = api_root.parent.parent  # apps/api -> repo root

    root_env = repo_root / ".env.example"
    api_env = api_root / ".env.example"
    compose_env = repo_root / "infra" / "compose" / ".env.compose.example"

    for label, path in (
        ("root .env.example", root_env),
        ("api .env.example", api_env),
        ("compose .env.compose.example", compose_env),
    ):
        if not path.exists():
            print(f"check_env_examples: missing {label}: {path}", file=sys.stderr)
            return 2

    root_keys = _extract_keys(root_env)
    backend_keys = _backend_only(root_keys)
    api_keys = _extract_keys(api_env)
    compose_keys = _extract_keys(compose_env)

    exit_code = 0

    # 1. apps/api must carry every backend key from root.
    api_missing = sorted(backend_keys - api_keys)
    if api_missing:
        _report("backend", "apps/api/.env.example", api_missing)
        exit_code = 1

    # 2. compose must carry every key from root (backend + NEXT_PUBLIC_*).
    #    Compose runs both the API and the web container, so the compose
    #    .env is a superset of the root contract.
    compose_missing = sorted(root_keys - compose_keys)
    if compose_missing:
        if exit_code:
            print("", file=sys.stderr)  # blank line between reports
        _report("backend + NEXT_PUBLIC_*", "infra/compose/.env.compose.example", compose_missing)
        exit_code = 1

    if exit_code == 0:
        print(
            f"check_env_examples: ok — {len(backend_keys)} backend keys agree "
            f"between root, apps/api, and compose ({len(root_keys)} keys total)"
        )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
