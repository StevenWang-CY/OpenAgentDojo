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
from datetime import UTC, datetime, timedelta
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

# Age guards on the orphan sweep: a row in "provisioning" / "submitting" is
# legitimately in-flight for a short window after the API crashes mid-step.
# Only flip the row when it has been stuck for at least this long. (Without
# this guard the sweeper could race a legitimately-starting session.)
_ORPHAN_PROVISIONING_GRACE_S = 5 * 60  # 5 minutes
_ORPHAN_SUBMITTING_GRACE_S = 15 * 60  # 15 minutes

# Dead-letter store of container ids whose ``destroy()`` raised. The pool
# release path bumps a handle into this set when the driver fails to tear it
# down; the orphan sweeper later retries ``docker rm -f`` so a transient
# driver error doesn't leak a container forever. Maps container_id ->
# handle_id so observability + logs can correlate back to the original lease.
_DEAD_LETTER: dict[str, str] = {}


def dead_letter_handles() -> dict[str, str]:
    """Return a snapshot of dead-letter ``{container_id: handle_id}`` mappings.

    Exposed for the orphan sweeper and operator tooling; returns a copy so
    iteration is safe even while ``release()`` is concurrently bumping new
    entries in.
    """
    return dict(_DEAD_LETTER)


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

    async def ping(self) -> bool:
        # Forward to the inner driver so a Docker daemon outage surfaces here
        # instead of the no-op default from ``SandboxDriver``.
        return await self._inner.ping()


class SandboxPool:
    """Concurrency-limited pool. One driver instance per pool."""

    def __init__(self, settings: Settings | None = None, driver: SandboxDriver | None = None):
        self.settings = settings or get_settings()
        raw_driver = driver or build_driver(self.settings)
        # Wrap so every I/O call refreshes ``last_activity_at`` automatically.
        self._driver = _ActivityTrackedDriver(raw_driver)
        self._semaphore = asyncio.Semaphore(self.settings.sandbox_max_concurrent)
        self._handles: dict[str, SandboxHandle] = {}
        # Reverse index for ``handle_for`` — keeps the linear scan off the hot
        # path of every workspace request (P1-B13).
        self._handles_by_session: dict[uuid.UUID, SandboxHandle] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def driver(self) -> SandboxDriver:
        return self._driver

    async def ping(self) -> bool:
        """Forward to the underlying driver — readiness probe convenience."""
        if self._closed:
            return False
        return await self._driver.ping()

    # --------------------------------------------------------------- acquire
    async def acquire(self, mission: Any, session_id: uuid.UUID) -> SandboxHandle:
        if self._closed:
            raise RuntimeError("sandbox pool is closed")

        # Defensive idempotency: a retry of provisioning (e.g. after a
        # transient driver failure) would otherwise leak the previous
        # handle — _handles_by_session would just overwrite, leaving the
        # by-id entry and the semaphore slot held forever. Release the
        # stale handle BEFORE the semaphore acquire so the retry can
        # reuse the slot.
        stale = self._handles_by_session.get(session_id)
        if stale is not None:
            logger.warning(
                "sandbox pool: stale handle for session {} — releasing before re-acquire",
                session_id,
            )
            try:
                await self.release(stale)
            except Exception as exc:
                logger.warning(
                    "sandbox pool: release of stale handle for session {} failed: {}",
                    session_id,
                    exc,
                )

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
            self._handles_by_session[handle.session_id] = handle
        sessions_active.inc()
        logger.debug("sandbox pool: acquired {} ({} active)", handle.id, len(self._handles))
        return handle

    async def release(self, handle: SandboxHandle) -> None:
        # Make sure we only destroy a handle the pool actually owns. We hold a
        # local reference and confirm membership under the lock, but we
        # intentionally DO NOT pop the indexes until ``destroy()`` returns —
        # otherwise a request that arrives between the pop and the destroy
        # would see "no handle for this session" and respond 503, even though
        # the sandbox is still mid-teardown and could in principle still
        # serve a final read. Keeping the handle visible until destroy
        # completes narrows that window from "however long destroy takes"
        # down to "post-destroy bookkeeping".
        async with self._lock:
            owned = self._handles.get(handle.id) is handle
        if not owned:
            return

        try:
            try:
                await self._driver.destroy(handle)
            except Exception as exc:
                # A destroy() failure means the underlying container did NOT
                # get torn down. We MUST still drop the handle from the pool
                # (otherwise the semaphore + slot leaks forever) but we also
                # record the orphaned container id so the orphan sweeper can
                # retry ``docker rm -f`` later. Log loudly — silent container
                # leaks bankrupt Docker hosts in production.
                # Prefer the dataclass field; some drivers (docker) only
                # populate ``driver_state["container_id"]``.
                container_id = getattr(handle, "container_id", None) or (
                    handle.driver_state.get("container_id")
                    if isinstance(handle.driver_state, dict)
                    else None
                )
                if container_id:
                    _DEAD_LETTER[str(container_id)] = handle.id
                logger.error(
                    "[sandbox] destroy FAILED for handle={} container={} — "
                    "container leaked; dead-lettered for orphan sweeper retry: {}",
                    handle.id,
                    container_id or "<unknown>",
                    exc,
                )
            finally:
                async with self._lock:
                    self._handles.pop(handle.id, None)
                    self._handles_by_session.pop(handle.session_id, None)
                self._semaphore.release()
                logger.debug(
                    "sandbox pool: released {} ({} active)",
                    handle.id,
                    len(self._handles),
                )
        finally:
            # ``finally`` so the gauge always returns to baseline — a raise
            # from any of the above (driver bug, lock acquire failure)
            # otherwise leaves ``sessions_active`` permanently inflated.
            sessions_active.dec()

    def get(self, handle_id: str) -> SandboxHandle | None:
        return self._handles.get(handle_id)

    def handle_for(self, session_id: uuid.UUID) -> SandboxHandle | None:
        """O(1) lookup of an active handle by ``session_id``."""
        return self._handles_by_session.get(session_id)

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
                # Best-effort dead-letter retry — independent from the DB
                # sweep so a DB outage doesn't block container reclamation.
                try:
                    await self._retry_dead_letter()
                except Exception as exc:  # pragma: no cover
                    logger.warning("dead-letter retry failed: {}", exc)
        except asyncio.CancelledError:
            pass

    async def _retry_dead_letter(self) -> None:
        """Retry ``docker rm -f`` on any container ids stranded by a destroy fail.

        Drains entries off ``_DEAD_LETTER`` one at a time; on success we drop
        the entry, on failure we leave it for the next pass. The driver
        exposes ``force_remove_container`` when it supports forceful removal;
        we fall back to a no-op when the wrapper lacks that hook (the local
        driver, used in tests, does not run containers).
        """
        if not _DEAD_LETTER:
            return
        snapshot = list(_DEAD_LETTER.items())
        force_remove = getattr(self._driver, "force_remove_container", None)
        if force_remove is None:
            # Try the inner driver too — ``_ActivityTrackedDriver`` delegates
            # via ``__getattr__`` but a custom wrapper may not.
            inner = getattr(self._driver, "inner", None) or getattr(self._driver, "_inner", None)
            if inner is not None:
                force_remove = getattr(inner, "force_remove_container", None)
        for container_id, handle_id in snapshot:
            try:
                if force_remove is None:
                    # No reclamation primitive available — drop the entry so
                    # we don't loop on it forever. The destroy fail is still
                    # in the structured log.
                    logger.warning(
                        "[sandbox] dead-letter entry container={} handle={} "
                        "dropped — driver has no force_remove_container hook",
                        container_id,
                        handle_id,
                    )
                    _DEAD_LETTER.pop(container_id, None)
                    continue
                await force_remove(container_id)
                _DEAD_LETTER.pop(container_id, None)
                logger.info(
                    "[sandbox] dead-letter retry SUCCESS — reclaimed container={} (handle={})",
                    container_id,
                    handle_id,
                )
            except Exception as exc:
                logger.warning(
                    "[sandbox] dead-letter retry failed for container={} (handle={}): {}",
                    container_id,
                    handle_id,
                    exc,
                )

    async def _sweep_orphans_once(self) -> int:  # noqa: PLR0912
        """Flip orphaned in-flight sessions to a terminal state.

        Three cases:

        * ``active``      → ``abandoned`` (clean teardown of an interactive
                            session whose pool handle vanished).
        * ``provisioning``→ ``abandoned`` if stuck for >5 minutes (the API
                            crashed before the sandbox was up).
        * ``submitting``  → ``error`` (NOT abandoned) if stuck for >15
                            minutes — the user submitted, the worker died,
                            and we want the FE to keep showing the diff /
                            report rather than a generic "abandoned" stub.

        Emits one supervision event per swept row so the FE / timeline can
        distinguish a clean teardown from a crashed worker.
        """
        live_session_ids = {h.session_id for h in self.handles_snapshot()}
        try:
            from app.db.session import AsyncSessionLocal
            from app.models.session import SessionRow
            from app.sessions.events import EventEmitter, get_redis
        except Exception:  # pragma: no cover
            return 0

        now = datetime.now(UTC)
        provisioning_cutoff = now - timedelta(seconds=_ORPHAN_PROVISIONING_GRACE_S)
        submitting_cutoff = now - timedelta(seconds=_ORPHAN_SUBMITTING_GRACE_S)

        try:
            redis = await get_redis()
            async with AsyncSessionLocal() as db:
                stmt = select(
                    SessionRow.id,
                    SessionRow.status,
                    SessionRow.started_at,
                ).where(SessionRow.status.in_(("active", "provisioning", "submitting")))
                rows = (await db.execute(stmt)).all()

                abandoned: list[uuid.UUID] = []
                errored: list[uuid.UUID] = []
                for sid, sstatus, started_at in rows:
                    if sid in live_session_ids:
                        continue
                    if sstatus == "active":
                        abandoned.append(sid)
                    elif sstatus == "provisioning":
                        if started_at is None or started_at < provisioning_cutoff:
                            abandoned.append(sid)
                    elif sstatus == "submitting":
                        if started_at is None or started_at < submitting_cutoff:
                            errored.append(sid)

                if not abandoned and not errored:
                    return 0

                if abandoned:
                    await db.execute(
                        update(SessionRow)
                        .where(SessionRow.id.in_(abandoned))
                        .values(status="abandoned", completed_at=now)
                    )
                if errored:
                    # ``submitting`` rows graduate to ``error`` so the existing
                    # report (if any) and the user-visible diff stay reachable.
                    # ``completed_at`` is still stamped so the row drops out of
                    # the "active sessions" cap.
                    await db.execute(
                        update(SessionRow)
                        .where(SessionRow.id.in_(errored))
                        .values(status="error", completed_at=now)
                    )

                emitter = EventEmitter(db=db, redis_client=redis)
                for sid in abandoned:
                    try:
                        await emitter.emit(
                            session_id=sid,
                            event_type="session.abandoned",
                            payload={"reason": "orphan_sweep"},
                            publish_after_commit=False,
                        )
                    except Exception as exc:  # pragma: no cover
                        logger.debug("session.abandoned emit failed for {}: {}", sid, exc)
                for sid in errored:
                    try:
                        await emitter.emit(
                            session_id=sid,
                            event_type="session.errored",
                            payload={"stage": "grading", "detail": "worker_crashed"},
                            publish_after_commit=False,
                        )
                    except Exception as exc:  # pragma: no cover
                        logger.debug("session.errored emit failed for {}: {}", sid, exc)

                await db.commit()
                logger.info(
                    "orphan sweep: marked {} abandoned + {} errored session rows",
                    len(abandoned),
                    len(errored),
                )
                return len(abandoned) + len(errored)
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
