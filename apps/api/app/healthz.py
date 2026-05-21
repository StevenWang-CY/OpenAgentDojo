"""Health probe endpoints.

* ``GET /healthz`` — liveness, cheap and minimal.
* ``GET /healthz/ready`` — readiness. Probes DB, Redis and (optionally) S3
  with hard 1-second timeouts each, and returns the active sandbox driver
  and the API version so deploy tooling can verify the running build.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from loguru import logger
from sqlalchemy import text

from app import __version__
from app.config import get_settings
from app.db.session import AsyncSessionLocal

router = APIRouter(tags=["health"])

# Hard upper bound on any single readiness probe.
_PROBE_TIMEOUT_S = 1.0


async def _db_ok() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.debug("healthz: db check failed: {}", exc)
        return False


async def _db_ok_bounded() -> bool:
    try:
        return await asyncio.wait_for(_db_ok(), timeout=_PROBE_TIMEOUT_S)
    except TimeoutError:
        logger.debug("healthz: db check timed out")
        return False


def _redis_ok() -> bool:
    settings = get_settings()
    try:
        import redis  # local import keeps cold-start cheap

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=_PROBE_TIMEOUT_S,
            socket_timeout=_PROBE_TIMEOUT_S,
        )
        return bool(client.ping())
    except Exception as exc:
        logger.debug("healthz: redis check failed: {}", exc)
        return False


async def _redis_ok_bounded() -> bool:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_redis_ok), timeout=_PROBE_TIMEOUT_S + 0.2)
    except TimeoutError:
        return False


def _s3_ok() -> bool:
    """Best-effort S3/MinIO check.

    Returns True when no S3 endpoint is configured (treat as "n/a → not blocking")
    so a dev box without object storage still reports ready.
    """
    settings = get_settings()
    if not settings.s3_endpoint_url:
        return True
    try:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(
                connect_timeout=_PROBE_TIMEOUT_S,
                read_timeout=_PROBE_TIMEOUT_S,
                retries={"max_attempts": 1},
            ),
        )
        client.head_bucket(Bucket=settings.s3_bucket)
        return True
    except Exception as exc:
        logger.debug("healthz: s3 check failed: {}", exc)
        return False


async def _s3_ok_bounded() -> bool:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_s3_ok), timeout=_PROBE_TIMEOUT_S + 0.2)
    except TimeoutError:
        return False


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "db": await _db_ok_bounded(),
        "redis": await _redis_ok_bounded(),
        "sandbox_driver": settings.sandbox_driver,
        "env": settings.arena_env,
    }


@router.get("/healthz/ready", summary="Readiness probe")
async def healthz_ready() -> dict[str, Any]:
    """Full readiness check with bounded per-probe timeouts."""
    settings = get_settings()
    db_ok, redis_ok, s3_ok = await asyncio.gather(
        _db_ok_bounded(),
        _redis_ok_bounded(),
        _s3_ok_bounded(),
    )
    return {
        "db": db_ok,
        "redis": redis_ok,
        "s3": s3_ok,
        "sandbox_driver": settings.sandbox_driver,
        "version": __version__,
    }
