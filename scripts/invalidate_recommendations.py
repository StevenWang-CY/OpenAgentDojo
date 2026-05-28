#!/usr/bin/env python3
"""One-shot CLI: invalidate every materialised user_recommendations row.

Run after a rubric rebalance / one-off catalog correction that the
loader's per-upsert hook can't catch (e.g. a mission's
``expected_weak_dim`` was edited in place outside the manifest loader,
or a new RUBRIC_VERSION shipped without a fresh manifest upsert).

Usage::

    python scripts/invalidate_recommendations.py

The script opens a single ``AsyncSession`` against the configured
database, stamps ``invalidated_at = now()`` on every row whose
``invalidated_at IS NULL``, commits, and prints the affected row count.
Idempotent — running again immediately is a no-op (the rows are
already stamped).

Exit codes:
  * ``0`` — success (rows were stamped, possibly zero).
  * ``1`` — DB or import failure; the message is logged and re-raised.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make the FastAPI app importable when invoked from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_ROOT = _REPO_ROOT / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))


async def _async_main() -> int:
    """Open a session, bulk-invalidate, commit, print count."""
    from loguru import logger

    from app.db.session import AsyncSessionLocal
    from app.recommendations.cache import bulk_invalidate_all

    async with AsyncSessionLocal() as db:
        try:
            count = await bulk_invalidate_all(db)
        except Exception as exc:  # pragma: no cover — surface to operator
            logger.error("bulk_invalidate_all failed: {}", exc)
            return 1
        await db.commit()
    print(f"invalidated {count} user_recommendations row(s)")
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())
