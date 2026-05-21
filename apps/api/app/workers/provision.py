"""Sandbox provisioning worker.

Enqueued on session creation; transitions the session through
``provisioning → active`` (or ``error``) and runs the mission's
``setup_commands`` + ``ready_check``.

Architecture note (P0-B6): the long-lived sandbox handle lives on
``app.state.sandbox_pool`` in the API process. RQ workers run in a separate
process and can't write to that pool, which would orphan every WS terminal /
file / diff call that follows. Until grading is moved to its own worker queue
(see plan §M8) we therefore *always* run provisioning in-process and treat
the RQ enqueue branch as opt-in via ``settings.provision_in_process=False``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import HTTPException
from loguru import logger

from app.config import get_settings
from app.workers.queue import get_queue

# Module-level handle on the running FastAPI app — populated by the lifespan
# hook in app.main. We avoid passing this through enqueue_provision so callers
# don't have to thread `request.app` everywhere.
_APP_REF: Any = None

# Strong refs to in-flight in-process provisioning tasks so the asyncio event
# loop cannot garbage-collect them mid-flight (the asyncio docs explicitly
# warn fire-and-forget ``create_task`` can be GC'd before completion). Tasks
# self-discard via ``add_done_callback`` once they finish.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _track_task(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    """Register ``task`` so the loop keeps a strong ref until it completes."""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def register_app(app: Any) -> None:
    """Wire the running app instance so in-process provisioning can reach it."""
    global _APP_REF  # noqa: PLW0603 — intentional module-level handle for the in-proc fallback
    _APP_REF = app


def enqueue_provision(session_id: uuid.UUID) -> None:
    """Schedule provisioning for ``session_id``.

    Default: run in-process so the resulting :class:`SandboxHandle` lands on
    the shared ``app.state.sandbox_pool``. Setting
    ``settings.provision_in_process=False`` re-enables the RQ enqueue path for
    future deployments that move the pool to a dedicated worker pod.
    """
    settings = get_settings()
    in_proc = getattr(settings, "provision_in_process", True)

    redis_error: Exception | None = None
    if not in_proc:
        queue = get_queue()
        if queue is not None:
            try:
                queue.enqueue("app.workers.provision.run_provision_job", str(session_id))
                return
            except Exception as exc:
                redis_error = exc
                logger.warning("RQ enqueue failed for {}: {}", session_id, exc)

    # In-process path: schedule on the running loop. Provisioning is async +
    # bounded by the sandbox driver's own timeouts.
    try:
        loop = asyncio.get_running_loop()
        _track_task(
            loop.create_task(
                _async_run_provision(session_id, in_process=True),
                name=f"provision-{session_id}",
            )
        )
        return
    except RuntimeError as exc:
        logger.error(
            "no event loop available for in-proc provision of {}: {}",
            session_id,
            exc,
        )

    # Both paths failed — escalate to the caller via HTTP 503.
    _mark_session_error_sync(session_id)
    detail = f"could not enqueue provision job: {redis_error or 'no fallback available'}"
    raise HTTPException(status_code=503, detail=detail)


def _mark_session_error_sync(session_id: uuid.UUID) -> None:
    """Best-effort sync DB update — used when async provisioning cannot start."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_mark_session_error(session_id))
            return
        _track_task(
            loop.create_task(
                _mark_session_error(session_id),
                name=f"mark-session-error-{session_id}",
            )
        )
    except Exception as exc:
        logger.warning("could not mark session {} as error: {}", session_id, exc)


async def _mark_session_error(session_id: uuid.UUID) -> None:
    from app.db.session import AsyncSessionLocal
    from app.sessions.service import set_status

    async with AsyncSessionLocal() as db:
        await set_status(db, session_id, "error")
        await db.commit()


def run_provision_job(session_id_str: str) -> None:
    """RQ entrypoint (must be import-safe and runnable in a fresh process)."""
    asyncio.run(_async_run_provision(uuid.UUID(session_id_str), in_process=False))


async def _async_run_provision(session_id: uuid.UUID, in_process: bool) -> None:
    from app.db.session import AsyncSessionLocal
    from app.sandbox.factory import build_driver
    from app.sessions.events import EventEmitter, get_redis
    from app.sessions.service import (
        get_session,
        set_status,
    )

    settings = get_settings()

    # When running in-process, prefer the shared pool so the WS terminal route
    # can find the sandbox handle. The pool owns its driver instance.
    pool = None
    if in_process and _APP_REF is not None:
        pool = getattr(_APP_REF.state, "sandbox_pool", None)

    driver = pool.driver if pool is not None else build_driver(settings)

    async with AsyncSessionLocal() as db:
        # Small retry — the in-process path schedules this task before the
        # router's get_db dependency has finished its commit.
        session = None
        for _ in range(20):  # ~2s @ 100ms
            session = await get_session(db, session_id)
            if session is not None:
                break
            await asyncio.sleep(0.1)
        if session is None:
            logger.warning("provision job: session {} not found", session_id)
            return

        redis = await get_redis()
        emitter = EventEmitter(db=db, redis_client=redis)

        manifest = _load_manifest_for(settings.missions_root, session.mission_id)
        if manifest is None:
            logger.warning(
                "provision job: manifest {} not found for session {}",
                session.mission_id,
                session_id,
            )
            await _emit_errored_and_mark(
                db, emitter, session_id, stage="manifest", detail="manifest not found"
            )
            return

        try:
            try:
                if pool is not None:
                    handle = await pool.acquire(manifest, session_id)
                else:
                    handle = await driver.provision(manifest, session_id)
            except Exception as exc:
                logger.exception("sandbox provision failed for {}: {}", session_id, exc)
                await _emit_errored_and_mark(
                    db, emitter, session_id, stage="sandbox", detail=str(exc)
                )
                return

            if not await _attach_sandbox(db, session_id, handle.id):
                return
            # Persist the sandbox handle before we start the (potentially slow)
            # ready check so a crash in ready_check still leaves a row that the
            # idle reaper can clean up.
            await db.commit()

            try:
                ready_ok = await _run_setup_commands(driver, handle, manifest, settings)
            except Exception as exc:
                logger.exception("ready_check raised for {}: {}", session_id, exc)
                await _emit_errored_and_mark(
                    db, emitter, session_id, stage="ready_check", detail=str(exc)
                )
                return

            if not ready_ok:
                logger.error(
                    "ready_check failed for {} — marking session error",
                    session_id,
                )
                await _emit_errored_and_mark(
                    db,
                    emitter,
                    session_id,
                    stage="ready_check",
                    detail="ready_check returned non-zero",
                )
                return

            # Ready_check OK → flip to active and announce. We deliberately
            # emit ``session.started`` AFTER the readiness gate so subscribers
            # never see a "started" event for a sandbox that is about to flip
            # to ``error`` (P1-B16).
            await set_status(db, session_id, "active")
            await emitter.emit(
                session_id=session_id,
                event_type="session.started",
                payload={
                    "mission_id": session.mission_id,
                    "initial_commit": getattr(manifest.repo, "initial_commit", None)
                    or getattr(manifest, "initial_commit", None),
                    "sandbox_driver": settings.sandbox_driver,
                },
                publish_after_commit=False,
            )
            await db.commit()
        except Exception as exc:  # safety net — should be unreachable
            logger.exception("provision pipeline failed for {}: {}", session_id, exc)
            await _emit_errored_and_mark(
                db, emitter, session_id, stage="provisioning", detail=str(exc)
            )


async def _attach_sandbox(db: Any, session_id: uuid.UUID, handle_id: str) -> bool:
    """Persist ``handle_id`` on the session row; return False if the row vanished.

    Extracted from :func:`_async_run_provision` so the function's statement
    count stays under the linter's PLR0915 ceiling. The boolean return is the
    "keep going" signal — False means the orphan sweeper (or a test) deleted
    the session between provision dispatch and handle availability, so the
    pool's idle reaper will clean up the now-unowned handle on its own.
    """
    from app.sessions.service import SessionNotFoundError, set_sandbox

    try:
        await set_sandbox(db, session_id, handle_id)
    except SessionNotFoundError:
        logger.warning(
            "provision job: session {} disappeared before sandbox could be attached",
            session_id,
        )
        return False
    return True


async def _emit_errored_and_mark(
    db: Any,
    emitter: Any,
    session_id: uuid.UUID,
    *,
    stage: str,
    detail: str,
) -> None:
    """Flip the session row to ``error`` and emit ``session.errored``."""
    from app.sessions.service import set_status

    try:
        await set_status(db, session_id, "error")
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("could not mark session {} as error: {}", session_id, exc)
    try:
        await emitter.emit(
            session_id=session_id,
            event_type="session.errored",
            payload={"stage": stage, "detail": detail[:500]},
            publish_after_commit=False,
        )
    except Exception as exc:  # pragma: no cover — telemetry only
        logger.warning("could not emit session.errored for {}: {}", session_id, exc)
    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover
        logger.warning("could not commit session.errored for {}: {}", session_id, exc)


def _load_manifest_for(root, mission_id: str) -> Any | None:
    from app.missions.loader import MissionLoader

    loader = MissionLoader(root)
    for m in loader.scan():
        if m.manifest.id == mission_id:
            return m.manifest
    return None


async def _run_setup_commands(driver, handle, manifest, settings) -> bool:
    """Run the mission's setup_commands and ready_check.

    Returns ``True`` when the ready_check succeeded (or none was declared) and
    ``False`` when ready_check exited non-zero. The caller is responsible for
    transitioning the session row to ``error`` on a False return.

    For the ``local`` driver we skip the setup commands by default — they
    typically install packages (``pnpm install --frozen-lockfile``) which
    requires network access the local-driver MVP does not guarantee (plan
    §9.4 — local is laptop-only with a "no isolation" warning). Repo packs
    ship with ``node_modules`` preinstalled so unit tests still run. Set
    ``ARENA_RUN_LOCAL_SETUP=1`` to override.
    """
    if manifest is None:
        return True

    import os

    skip_setup = settings.sandbox_driver == "local" and not os.environ.get("ARENA_RUN_LOCAL_SETUP")
    if skip_setup:
        logger.info("local driver: skipping setup_commands (repo pack must ship dependencies)")
    else:
        for cmd in getattr(manifest.repo, "setup_commands", []) or []:
            logger.debug("running setup command: {}", cmd)
            await driver.run(handle, ["/bin/sh", "-c", cmd], timeout_s=300)

    ready = getattr(manifest.repo, "ready_check", None)
    if ready:
        result = await driver.run(handle, ["/bin/sh", "-c", ready], timeout_s=120)
        if result.exit_code != 0:
            logger.warning("ready_check failed: {}", result.stderr)
            return False
    return True
