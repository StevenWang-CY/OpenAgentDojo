#!/usr/bin/env python3
"""Process every account whose deletion grace has expired.

Designed to be invoked from a cron / Fly cron schedule once per day at
roughly 03:00 UTC (off-peak; gives the day's grace expiries a few hours
to roll over before the next pass). The function is idempotent — a
second invocation in the same window finds zero rows and exits 0.

Scheduling
----------
The compose stack runs this script on a daily cron via the dedicated
``deletion-grace-cron`` service in ``infra/compose/docker-compose.yml``
(an Alpine container running BusyBox crond). The service emits
``account_deletion_grace_run_total{result="success|partial|failed"}``
per invocation so a Prometheus rule "no success tick in 36h" catches a
wedged sweeper.

Production deployments that don't run compose should wire an equivalent
schedule via their orchestrator (Fly machines cron, k8s CronJob, AWS
EventBridge, ...) — the script is a single command and takes no args.

Usage
-----
::

    # Once-off from the API workdir
    uv run python apps/api/scripts/process_deletion_grace.py

    # Crontab — once a day at 03:00 UTC
    0 3 * * * cd /app && uv run python apps/api/scripts/process_deletion_grace.py

Exit codes
----------
``0`` on every successful run (including "no eligible rows"). Non-zero
only when the database is unreachable or the import path is broken,
which would also turn into a Sentry / Loki alert via the worker's
loguru sink.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_app_importable() -> Path:
    """Add ``apps/api`` to sys.path so ``app`` resolves when run directly."""
    here = Path(__file__).resolve()
    api_root = here.parent.parent  # apps/api/scripts/file -> apps/api
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    return api_root


def main() -> int:
    _ensure_app_importable()
    from loguru import logger

    from app.workers.account_deletion import process_deletion_grace

    try:
        processed = process_deletion_grace()
    except Exception as exc:  # pragma: no cover — surface to CI / Sentry
        logger.exception("process_deletion_grace failed: {}", exc)
        return 1
    print(f"process_deletion_grace: {processed} account(s) hard-deleted")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
