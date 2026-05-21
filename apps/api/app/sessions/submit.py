"""Submit-session business logic (M5).

Thin shim that locates the sandbox handle + manifest folder, then delegates
to :class:`GradingRunner.run_and_persist` which handles status transitions,
event emission, and the budget timeout.

Two reliability fixes (P0-B2 / P0-B3):

* Concurrent-submit race — we use an atomic ``UPDATE ... WHERE status='active'``
  to flip the row to ``submitting``; if the row count is zero some other
  request already grabbed the session and we reject with 409.
* Timeout fallback — on ``TimeoutError`` we still write a stub Submission row
  with score 0 so ``GET /sessions/{id}/submission`` returns a meaningful 200
  with the failure surfaced via ``session.status``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from loguru import logger
from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.grading.runner import GradingRunner
from app.missions.loader import MissionLoader
from app.missions.resolver import MissionFolderNotFoundError, resolve_mission_dir
from app.models.session import SessionRow
from app.models.submission import Submission
from app.schemas.submission import SubmissionRead

# Terminal statuses that should reject a new submit with 409.
_TERMINAL_STATUSES = frozenset({"submitting", "graded"})


def _find_manifest_folder(settings: Any, mission_id: str) -> Path:
    """Locate the mission folder for ``mission_id`` (``NN-{mission_id}`` or exact)."""
    missions_root: Path = settings.missions_root
    try:
        folder = resolve_mission_dir(missions_root, mission_id)
    except (FileNotFoundError, ValueError, MissionFolderNotFoundError) as exc:
        raise LookupError(
            f"mission folder for '{mission_id}' not found under {missions_root}"
        ) from exc
    # Submit additionally requires the mission.yaml to be present — without it
    # there's nothing for the loader to read.
    if not (folder / "mission.yaml").exists():
        raise LookupError(
            f"mission folder for '{mission_id}' has no mission.yaml under {folder}"
        )
    return folder


def _locate_handle(request: Request, session_id: uuid.UUID) -> Any:
    """Return the sandbox handle for ``session_id`` or None."""
    pool = getattr(request.app.state, "sandbox_pool", None)
    if pool is None:
        return None
    # O(1) lookup when the pool exposes `handle_for`.
    if hasattr(pool, "handle_for"):
        return pool.handle_for(session_id)
    snapshot = pool.handles_snapshot()
    for h in snapshot:
        if str(getattr(h, "session_id", "")) == str(session_id):
            return h
    return None


def _resolve_driver(request: Request) -> Any:
    pool = getattr(request.app.state, "sandbox_pool", None)
    if pool is None:
        return None
    driver = getattr(pool, "driver", None)
    return driver if driver is not None else pool


async def _claim_for_submit(db: AsyncSession, session: SessionRow) -> bool:
    """Atomically flip status ``active → submitting``.

    Returns True when this caller claimed the session, False when another
    request beat us to it. Uses a single UPDATE with a guard predicate so the
    semantics work on SQLite (no SELECT ... FOR UPDATE).

    Also stamps ``last_activity_at`` so the idle reaper treats the submit
    handshake as a fresh interaction — the heavy grading work that follows
    must not get sniped by the orphan sweeper while it runs.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        update(SessionRow)
        .where(SessionRow.id == session.id, SessionRow.status == "active")
        .values(status="submitting", last_activity_at=now)
    )
    # Runtime guard (not ``assert`` — that vanishes under ``python -O``). UPDATE
    # statements always yield a ``CursorResult``, but we still verify so a type
    # mismatch fails loudly with structured logging rather than a stray
    # AttributeError on ``.rowcount``.
    if not isinstance(result, CursorResult):
        raise RuntimeError(f"expected CursorResult from UPDATE, got {type(result).__name__}")
    if result.rowcount == 0:
        return False
    # Reflect the new values on the ORM instance for downstream code.
    session.status = "submitting"
    session.last_activity_at = now
    return True


async def submit_session(
    db: AsyncSession,
    session: SessionRow,
    request: Request,
) -> SubmissionRead:
    """Run the grading pipeline for ``session`` and return a SubmissionRead."""
    settings = get_settings()
    session_id: uuid.UUID = session.id

    # Cheap up-front check so we 409 fast and don't even bother looking up the
    # sandbox handle when the session is already terminal.
    if session.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"session is already in state '{session.status}'",
        )

    driver = _resolve_driver(request)
    if driver is None:
        raise HTTPException(status_code=503, detail="sandbox pool unavailable")

    # Atomic claim FIRST — reject concurrent submitters with 409 BEFORE any
    # status='error' mutation. The previous ordering (look up handle/manifest
    # → mark error → claim) let a transient lookup miss flip the session row
    # into ``error`` even when another caller already owned the submit.
    claimed = await _claim_for_submit(db, session)
    if not claimed:
        # Re-fetch the row so we surface the current (raced) status without
        # mutating it.
        await db.refresh(session)
        raise HTTPException(
            status_code=409,
            detail=f"session is already in state '{session.status}'",
        )
    # Persist the claim before kicking off the runner so a crashed runner
    # doesn't leave the session stuck in 'active' for another submitter to
    # grab.
    await db.commit()

    # Now that we own the row, any failure path below can safely flip status
    # to ``error`` without racing another submitter.
    handle = _locate_handle(request, session_id)
    if handle is None:
        logger.error("[submit] sandbox handle not found for session {}", session_id)
        session.status = "error"
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="sandbox not available — session may have expired",
        )

    try:
        manifest_folder = _find_manifest_folder(settings, session.mission_id)
        loader = MissionLoader(settings.missions_root)
        loaded = loader._load_one(manifest_folder / "mission.yaml")
        manifest = loaded.manifest
    except Exception as exc:
        logger.error("[submit] failed to load manifest for {}: {}", session.mission_id, exc)
        session.status = "error"
        await db.commit()
        raise HTTPException(
            status_code=422,
            detail=f"could not load mission manifest: {exc}",
        ) from exc

    runner = GradingRunner(settings)
    try:
        submission, _result = await runner.run_and_persist(
            db=db,
            session=session,
            driver=driver,
            handle=handle,
            manifest=manifest,
            manifest_folder=manifest_folder,
        )
    except TimeoutError as exc:
        # Stub a Submission row so GET /submission returns 200 instead of 404,
        # with the failure surfaced via session.status='error' (P1-B17).
        await _ensure_failed_stub(db, session_id, reason=f"timeout: {exc}")
        raise HTTPException(
            status_code=504,
            detail=f"grading exceeded budget: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("[submit] grading pipeline failed for session {}", session_id)
        await _ensure_failed_stub(db, session_id, reason=f"pipeline_error: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"grading pipeline error: {exc}",
        ) from exc

    logger.info(
        "[submit] session {} graded — score={}",
        session_id,
        submission.total_score,
    )
    return SubmissionRead.model_validate(submission)


async def _ensure_failed_stub(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    reason: str,
) -> Submission | None:
    """Persist a placeholder ``submissions`` row so GET returns 200.

    The grading pipeline can raise mid-transaction (validator crash, driver
    timeout) which leaves the AsyncSession in an aborted state on Postgres —
    the very next ``execute`` would otherwise raise ``InFailedSQLTransaction``
    before we ever get to the INSERT. Rolling back up-front clears that
    state so this best-effort stub-writer has a clean slate to work with.
    """
    from sqlalchemy import select

    try:
        await db.rollback()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("[submit] rollback before stub failed for {}: {}", session_id, exc)

    # If the runner managed to write a row already (e.g. validator crash before
    # commit failure) leave it alone — we don't want to clobber real data.
    try:
        existing = (
            await db.execute(select(Submission).where(Submission.session_id == session_id))
        ).scalar_one_or_none()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[submit] could not query existing submission for {}: {}", session_id, exc)
        existing = None
    if existing is not None:
        return existing
    try:
        stub = Submission(
            session_id=session_id,
            final_diff="",
            visible_test_results={},
            hidden_test_results={},
            validator_results={},
            score_report={
                "total": 0,
                "dimensions": {},
                "strengths": [],
                "weaknesses": [],
                "missed_failure_mode": True,
                "badges_earned": [],
                "failure_reason": reason[:500],
            },
            total_score=0,
        )
        db.add(stub)
        await db.commit()
        return stub
    except IntegrityError as exc:
        # Another writer raced us — clear the failed tx so a follow-up SELECT
        # works, then surface None so the caller can continue with its
        # original exception.
        logger.warning(
            "[submit] failure stub insert raced for {}: {}", session_id, exc
        )
        try:
            await db.rollback()
        except Exception as inner_exc:  # pragma: no cover — defensive
            logger.debug(
                "[submit] rollback after IntegrityError failed for {}: {}",
                session_id,
                inner_exc,
            )
        return None
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[submit] could not write failure stub for {}: {}", session_id, exc)
        try:
            await db.rollback()
        except Exception as inner_exc:  # pragma: no cover — defensive
            logger.debug(
                "[submit] rollback after stub error failed for {}: {}",
                session_id,
                inner_exc,
            )
        return None
