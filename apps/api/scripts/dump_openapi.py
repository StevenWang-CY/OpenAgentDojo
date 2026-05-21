#!/usr/bin/env python3
"""Dump the FastAPI OpenAPI schema to ``apps/api/openapi.json``.

This script is the single source of truth for the contract that
``packages/shared-types`` consumes. It is intentionally simple and
idempotent: importing the app and serialising the schema produces the
same JSON for the same input, modulo Python set ordering — which is
why we always pass ``sort_keys=True``.

Invocation
----------
From inside ``apps/api`` (preferred — `app` is on sys.path)::

    uv run python -m app.scripts.dump_openapi
    uv run python scripts/dump_openapi.py

From the repo root::

    uv --project apps/api run python apps/api/scripts/dump_openapi.py

The script writes to ``apps/api/openapi.json`` (one directory up from
``scripts/``) and prints the absolute output path.  Exit code is 0 on
success and non-zero on any import / serialisation failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _ensure_app_importable() -> Path:
    """Make ``app`` importable when this script is run directly.

    Returns the resolved ``apps/api`` directory.
    """
    here = Path(__file__).resolve()
    # apps/api/scripts/dump_openapi.py -> apps/api
    api_root = here.parent.parent
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    return api_root


def build_openapi() -> dict:
    """Import the FastAPI app and return its OpenAPI dict."""
    # Import lazily so the sys.path tweak in _ensure_app_importable() takes
    # effect before we try to resolve `app.main`.
    from app.main import app

    schema = app.openapi()
    if not isinstance(schema, dict):  # pragma: no cover — defensive
        raise RuntimeError("FastAPI returned a non-dict openapi schema")
    return schema


def dump_openapi(api_root: Path | None = None) -> Path:
    """Write the OpenAPI schema to ``apps/api/openapi.json`` and return the path."""
    api_root = api_root or _ensure_app_importable()
    out_path = api_root / "openapi.json"

    schema = build_openapi()

    # Stable, deterministic JSON: sort keys + LF line endings + final newline.
    rendered = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False)
    out_path.write_text(rendered + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    api_root = _ensure_app_importable()
    try:
        out_path = dump_openapi(api_root)
    except Exception as exc:  # pragma: no cover — surface to CI
        print(f"FAIL could not dump openapi: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
