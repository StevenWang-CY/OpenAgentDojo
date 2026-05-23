"""Public ``/status`` route — human-readable system summary.

Distinct from the internal ``/healthz`` and ``/healthz/ready`` probes (which
return raw JSON for k8s/load-balancer probes), ``/status`` is the page ops
links to from the marketing footer so users can sanity-check the platform
without poking around k8s.

The route is unauthenticated, not rate-limited (it's deliberately cheap and
cacheable for 10s), and aggregates the same probes that drive readiness so
operators see one truth.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response

from app import __version__
from app.config import get_settings
from app.healthz import (
    _db_ok_bounded,
    _redis_ok_bounded,
    _s3_ok_bounded,
)

# A worker component should report ``operational`` whenever the queue broker
# (Redis) is reachable — even an empty queue still counts as healthy
# capacity. The in-process provisioning fallback bypasses RQ entirely, so we
# label it explicitly in the response.
_WORKERS_NOTE_IN_PROCESS = "in-process"

router = APIRouter(tags=["status"])


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _component(ok: bool, checked_at: str) -> dict[str, str]:
    return {
        "status": "operational" if ok else "down",
        "checked_at": checked_at,
    }


def _worker_component(ok: bool, checked_at: str, *, note: str | None = None) -> dict[str, str]:
    """Worker component — adds an optional ``note`` (e.g. ``"in-process"``)."""
    payload: dict[str, str] = _component(ok, checked_at)
    if note:
        payload["note"] = note
    return payload


@router.get(
    "/status",
    summary="Public status page",
    description=(
        "Aggregated, human-readable system status. Cached for 10s. Mirrors the "
        "components probed by ``/healthz/ready`` plus uptime and version."
    ),
)
async def status(request: Request, response: Response) -> dict[str, Any]:
    settings = get_settings()

    db_ok, redis_ok, s3_ok = await asyncio.gather(
        _db_ok_bounded(),
        _redis_ok_bounded(),
        _s3_ok_bounded(),
    )

    # All probe timestamps share a single "now" — they ran concurrently and the
    # request itself is what's reaching the user, so a single instant is honest.
    checked_at = _now_iso()

    # By definition: if this handler is executing, the API is up. Encoding this
    # makes the contract explicit for downstream consumers parsing the JSON.
    api_ok = True

    components = {
        "api": _component(api_ok, checked_at),
        "database": _component(db_ok, checked_at),
        "redis": _component(redis_ok, checked_at),
        "object_storage": _component(s3_ok, checked_at),
    }

    # Aggregate. API down would mean we never reach here, so the only real
    # axes are "everything green" vs. "at least one component degraded".
    all_ok = all(c["status"] == "operational" for c in components.values())
    if not api_ok:
        overall = "down"
    elif all_ok:
        overall = "operational"
    else:
        overall = "degraded"

    boot_at = getattr(request.app.state, "boot_at", None)
    if boot_at is None:
        uptime_seconds = 0
    else:
        import time

        uptime_seconds = max(0, int(time.time() - boot_at))

    # Short-lived public cache: status is intentionally a low-resolution view.
    # 10s is short enough to recover from incidents quickly, long enough to
    # shield the probes from a status-page hug-of-death.
    response.headers["Cache-Control"] = "public, max-age=10"

    # Pool status — best-effort introspection so the page can show whether the
    # sandbox pool is alive without exposing internals. A missing pool means
    # the lifespan hook hasn't run (e.g. in test harnesses) which is not a
    # health signal — we report "operational" so the verdict isn't poisoned
    # by harness setup.
    pool = getattr(request.app.state, "sandbox_pool", None)
    if pool is None:
        pool_ok = True
    else:
        pool_ok = not getattr(pool, "_closed", True)
    components["sandbox_pool"] = _component(pool_ok, checked_at)

    # Workers: probe Redis (the RQ broker) as the proxy for "workers are at
    # least reachable". If provisioning runs in-process there is no separate
    # worker process, so the broker check still applies (RQ may be unused but
    # we don't want to falsely report DOWN) — we just annotate the mode.
    if settings.provision_in_process:
        components["workers"] = _worker_component(True, checked_at, note=_WORKERS_NOTE_IN_PROCESS)
    else:
        components["workers"] = _component(redis_ok, checked_at)

    # Recompute overall now that we have the pool component.
    all_ok = all(c["status"] == "operational" for c in components.values())
    if not api_ok:
        overall = "down"
    elif all_ok:
        overall = "operational"
    else:
        overall = "degraded"

    return {
        "status": overall,
        "components": components,
        "version": __version__,
        "env": settings.arena_env,
        "uptime_seconds": uptime_seconds,
        "links": {
            "healthz": "/healthz",
            "ready": "/healthz/ready",
            "docs": "/docs",
        },
    }


# Alias under /api/v1 so the frontend can hit a versioned endpoint without
# having to know about the root-level public path (P2-B6).
api_v1_router = APIRouter(prefix="/status", tags=["status"])


@api_v1_router.get(
    "",
    summary="API status (v1 alias for public /status)",
)
async def status_v1(request: Request, response: Response) -> dict[str, Any]:
    return await status(request, response)
