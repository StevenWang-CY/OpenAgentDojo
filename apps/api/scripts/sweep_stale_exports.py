#!/usr/bin/env python3
"""Mark data-export rows as ``failed`` when their worker died mid-build.

A ``running`` row whose ``requested_at`` is older than the configured
cutoff (default 15 minutes) is almost certainly the residue of an RQ
worker that crashed or was OOM-killed between the ``queued → running``
transition and the terminal ``ready / failed`` write. Left alone the row
blocks the user from kicking off a fresh export (the "one in flight per
user" guard treats it as still alive), AND the user sees a spinner on
``/account`` that never resolves.

This sweeper flips every such row to ``failed`` with
``error='worker_lost'`` so the FE can render a "retry" CTA on the next
GET. Idempotent: a second pass over the same rows is a no-op because
they're no longer in ``running`` state.

Cutoff rationale
----------------
We use ``requested_at`` (not a separate ``running_since`` column) as the
proxy timestamp because adding a column would require a migration and
the precision gain is marginal — the only race is "the worker started
14 minutes ago and is still legitimately running". The default 15-minute
cutoff is comfortably above the slowest realistic export build (single-
digit minutes even for power users with thousands of sessions).

Scheduling
----------
Runs on the same compose cron as the deletion-grace sweeper (see
``infra/compose/docker-compose.yml`` → ``stale-exports-cron``). Daily is
plenty for a defence-in-depth control; ops can override the cutoff via
``ARENA_STALE_EXPORT_CUTOFF_MINUTES`` when re-running ad-hoc.

Usage
-----
::

    uv run python apps/api/scripts/sweep_stale_exports.py
    uv run python apps/api/scripts/sweep_stale_exports.py --cutoff-minutes 60
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _ensure_app_importable() -> Path:
    here = Path(__file__).resolve()
    api_root = here.parent.parent
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    return api_root


DEFAULT_CUTOFF_MINUTES = 15


async def sweep_stale_exports(*, cutoff_minutes: int = DEFAULT_CUTOFF_MINUTES) -> int:
    """Flip stale ``running`` rows to ``failed`` and return the count flipped.

    The exact SQL is a single ``UPDATE ... WHERE status='running' AND
    requested_at < cutoff RETURNING id`` so we get the row count without
    a follow-up SELECT.
    """
    from sqlalchemy import update

    from app.db.session import AsyncSessionLocal
    from app.models.data_export import (
        EXPORT_STATUS_FAILED,
        EXPORT_STATUS_RUNNING,
        DataExport,
    )

    cutoff = datetime.now(UTC) - timedelta(minutes=cutoff_minutes)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(DataExport)
            .where(
                DataExport.status == EXPORT_STATUS_RUNNING,
                DataExport.requested_at < cutoff,
            )
            .values(status=EXPORT_STATUS_FAILED, error="worker_lost")
            .returning(DataExport.id)
        )
        rows = result.fetchall()
        await db.commit()
        return len(rows)


def main() -> int:
    _ensure_app_importable()
    from loguru import logger

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cutoff-minutes",
        type=int,
        default=int(os.environ.get("ARENA_STALE_EXPORT_CUTOFF_MINUTES", DEFAULT_CUTOFF_MINUTES)),
        help=(
            "Rows older than this many minutes in 'running' state are flipped "
            "to 'failed'. Default 15 (well above the slowest realistic build)."
        ),
    )
    args = parser.parse_args()

    try:
        flipped = asyncio.run(sweep_stale_exports(cutoff_minutes=args.cutoff_minutes))
    except Exception as exc:  # pragma: no cover — surfaces to CI / Sentry
        logger.exception("sweep_stale_exports failed: {}", exc)
        return 1
    print(f"sweep_stale_exports: {flipped} stale row(s) flipped to failed")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
