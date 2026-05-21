"""Bounded sandbox pool with idle reaper + orphan sweeper.

The pool centralises concurrency control (a semaphore over
``SANDBOX_MAX_CONCURRENT``) and exposes ``acquire`` / ``release`` so callers
never construct drivers directly.

Activity tracking
-----------------
Every driver call (``run``, ``read_file``, ``write_file``, ``apply_diff``,
``attach_shell``) updates ``handle.driver_state["last_activity_at"]`` via the
``_ActivityTrackedDriver`` wrapper. The reaper compares ``now - last_activity``
against ``sandbox_timeout_seconds`` instead of ``now - created_at`` so an
active user is never reaped mid-flow.

Sessions with ``status in {'graded', 'submitting'}`` are explicitly skipped so
the grading pipeline can finish writing artifacts.

Orphan sweeper
--------------
Every five minutes we scan the ``sessions`` table for rows still marked
``active`` whose ``id`` is not present in :meth:`handles_snapshot`. Those rows
are flipped to ``abandoned`` with ``completed_at = now()``. This catches API
crashes where the pool was torn down without the DB row being updated.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from app.config import Settings, get_settings
from app.observability import sessions_active, sessions_provision_seconds
from app.sandbox.driver import SandboxDriver
from app.sandbox.factory import build_driver
from app.sandbox.types import SandboxHandle

# Statuses that block the reaper from killing a handle even if it looks idle.
_DO_NOT_REAP_STATUSES = frozenset({"graded", "submitting"})

# Default cadence for the orphan sweeper.
_ORPHAN_SWEEP_INTERVAL_S = 300


def _touch(handle: SandboxHandle) -> None:
    """Stamp ``handle.driver_state['last_activity_at']`` with the current time."""
    handle.driver_state["last_activity_at"] = datetime.now(UTC)


def _last_activity(handle: SandboxHandle) -> datetime:
    """Read the handle's last activity timestamp, falling back to ``created_at``."""
    ts = handle.driver_state.get("last_activity_at")
    if isinstance(ts, datetime):
        return ts
    return handle.created_at


class _ActivityTrackedDriver(SandboxDriver):
    """Driver decorator that stamps ``last_activity_at`` on every I/O call.

    We forward everything else verbatim. The wrapped driver still owns the
    underlying connection / container; we just observe.
    """

    def __init__(self, inner: SandboxDriver) -> None:
        self._inner = inner

    @property
    def inner(self) -> SandboxDriver:
        return self._inner

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._inner.name

    # Delegate any non-tracked attribute (e.g. ``close_pty`` on the local driver).
    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    async def provision(self, mission, session_id) -> SandboxHandle:
        handle = await self._inner.provision(mission, session_id)
        _touch(handle)
        return handle

    async def attach_shell(self, handle):
        _touch(handle)
        return await self._inner.attach_shell(handle)

    async def read_file(self, handle, path):
        _touch(handle)
        return await self._inner.read_file(handle, path)

    async def write_file(self, handle, path, content):
        _touch(handle)
        return await self._inner.write_file(handle, path, content)

    async def list_tree(self, handle, root="/workspace"):
        _touch(handle)
        return await self._inner.list_tree(handle, root)

    async def diff_from_initial(self, handle):
        _touch(handle)
        return await self._inner.diff_from_initial(handle)

    async def run(self, handle, cmd, timeout_s=60, cwd=None):
        _touch(handle)
        return await self._inner.run(handle, cmd, timeout_s=timeout_s, cwd=cwd)

    async def apply_diff(self, handle, diff_text):
        _touch(handle)
        return await self._inner.apply_diff(handle, diff_text)

    async def freeze_and_grade(self, handle, mission, **kwargs):
        _touch(handle)
        return await self._inner.freeze_and_grade(handle, mission, **kwargs)

    async def destroy(self, handle):
        return await self._inner.destroy(handle)


class SandboxPool:
    """Concurrency-limited pool. One driver instance per pool."""

    def __init__(self, settings: Settings | None = None, driver: SandboxDriver | None = None):
        self.settings = settings or get_settings()
        raw_driver = driver or build_driver(self.settings)
        # Wrap so every I/O call refreshes ``last_activity_at`` automatically.
        self._driver = _ActivityTrackedDriver(raw_driver)
        self._semaphore = asyncio.Semaphore(self.settings.sandbox_max_concurrent)
        self._handles: dict[str, SandboxHandle] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def driver(self) -> SandboxDriver:
        return self._driver

    # --------------------------------------------------------------- acquire
    async def acquire(self, mission: Any, session_id: uuid.UUID) -> SandboxHandle:
        if self._closed:
            raise RuntimeError("sandbox pool is closed")

        await self._semaphore.acquire()
        started = time.perf_counter()
        try:
            handle = await self._driver.provision(mission, session_id)
        except Exception:
            self._semaphore.release()
            raise
        finally:
            sessions_provision_seconds.observe(time.perf_counter() - started)

        async with self._lock:
            self._handles[handle.id] = handle
        sessions_active.inc()
        logger.debug("sandbox pool: acquired {} ({} active)", handle.id, len(self._handles))
        return handle

    async def release(self, handle: SandboxHandle) -> None:
        async with self._lock:
            existed = self._handles.pop(handle.id, None) is not None

        if existed:
            sessions_active.dec()
            try:
                await self._driver.destroy(handle)
            except Exception as exc:
                logger.warning("sandbox destroy failed for {}: {}", handle.id, exc)
            finally:
                self._semaphore.release()
                logger.debug("sandbox pool: released {} ({} active)", handle.id, len(self._handles))

    def get(self, handle_id: str) -> SandboxHandle | None:
        return self._handles.get(handle_id)

    def handles_snapshot(self) -> list[SandboxHandle]:
        """Return a copy of the active handles — safe to iterate concurrently."""
        return list(self._handles.values())

    # ----------------------------------------------------------------- reap
    async def reaper_loop(self, interval_s: int = 60) -> None:
        """Background task — kill sandboxes idle past ``sandbox_timeout_seconds``."""
        try:
            while not self._closed:
                await asyncio.sleep(interval_s)
                await self._reap_once()
        except asyncio.CancelledError:
            pass

    async def _reap_once(self) -> None:
        ttl = self.settings.sandbox_timeout_seconds
        now = datetime.now(UTC)
        async with self._lock:
            candidates = list(self._handles.values())

        if not candidates:
            return

        graded_set = await self._sessions_with_status(
            {h.session_id for h in candidates}, _DO_NOT_REAP_STATUSES
        )

        expired: list[SandboxHandle] = []
        for h in candidates:
            if h.session_id in graded_set:
                continue
            if (now - _last_activity(h)).total_seconds() > ttl:
                expired.append(h)

        for h in expired:
            logger.info(
                "reaping idle sandbox {} (mission={}, idle_seconds={:.0f})",
                h.id,
                h.mission_id,
                (now - _last_activity(h)).total_seconds(),
            )
            await self.release(h)

    async def _sessions_with_status(
        self, session_ids: set[uuid.UUID], statuses: frozenset[str]
    ) -> set[uuid.UUID]:
        """Return the subset of ``session_ids`` whose DB row matches any status."""
        if not session_ids:
            return set()
        try:
            from app.db.session import AsyncSessionLocal
            from app.models.session import SessionRow

            async with AsyncSessionLocal() as db:
                stmt = (
                    select(SessionRow.id)
                    .where(SessionRow.id.in_(session_ids))
                    .where(SessionRow.status.in_(statuses))
                )
                result = await db.execute(stmt)
                return {row[0] for row in result.all()}
        except Exception as exc:  # pragma: no cover — DB outage shouldn't crash reaper
            logger.debug("reaper status lookup failed: {}", exc)
            return set()

    # ------------------------------------------------------------- orphans
    async def orphan_sweeper_loop(self, interval_s: int = _ORPHAN_SWEEP_INTERVAL_S) -> None:
        """Mark abandoned 'active' DB rows that have no live pool handle."""
        try:
            while not self._closed:
                await asyncio.sleep(interval_s)
                try:
                    await self._sweep_orphans_once()
                except Exception as exc:  # pragma: no cover
                    logger.warning("orphan sweep failed: {}", exc)
        except asyncio.CancelledError:
            pass

    async def _sweep_orphans_once(self) -> int:
        """Flip orphaned 'active' sessions to 'abandoned'. Returns number updated.

        Emits one ``session.abandoned`` supervision event per swept row so the
        FE / timeline can distinguish a clean teardown from a crashed worker.
        """
        live_session_ids = {h.session_id for h in self.handles_snapshot()}
        try:
            from app.db.session import AsyncSessionLocal
            from app.models.session import SessionRow
            from app.sessions.events import EventEmitter, get_redis
        except Exception:  # pragma: no cover
            return 0

        try:
            redis = await get_redis()
            async with AsyncSessionLocal() as db:
                stmt = select(SessionRow.id).where(SessionRow.status == "active")
                rows = (await db.execute(stmt)).scalars().all()
                orphans = [sid for sid in rows if sid not in live_session_ids]
                if not orphans:
                    return 0
                await db.execute(
                    update(SessionRow)
                    .where(SessionRow.id.in_(orphans))
                    .values(status="abandoned", completed_at=datetime.now(UTC))
                )
                emitter = EventEmitter(db=db, redis_client=redis)
                for sid in orphans:
                    try:
                        await emitter.emit(
                            session_id=sid,
                            event_type="session.abandoned",
                            payload={"reason": "orphan_sweep"},
                            publish_after_commit=False,
                        )
                    except Exception as exc:  # pragma: no cover
                        logger.debug("session.abandoned emit failed for {}: {}", sid, exc)
                await db.commit()
                logger.info("orphan sweep: marked {} abandoned session rows", len(orphans))
                return len(orphans)
        except Exception as exc:  # pragma: no cover
            logger.warning("orphan sweep DB error: {}", exc)
            return 0

    # ----------------------------------------------------------------- shut
    async def shutdown(self) -> None:
        self._closed = True
        async with self._lock:
            handles = list(self._handles.values())
        for h in handles:
            await self.release(h)
