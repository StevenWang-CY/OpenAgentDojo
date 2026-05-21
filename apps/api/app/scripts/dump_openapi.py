"""``python -m app.scripts.dump_openapi`` shim.

Delegates to :mod:`scripts.dump_openapi` so we have one implementation
regardless of how the script is invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _api_root() -> Path:
    # app/scripts/dump_openapi.py -> apps/api
    return Path(__file__).resolve().parent.parent.parent


def main() -> int:
    api_root = _api_root()
    scripts_dir = api_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from dump_openapi import main as _main  # type: ignore[import-not-found]

    rc: int = _main()
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
