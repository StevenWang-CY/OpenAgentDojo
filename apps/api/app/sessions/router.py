"""Session REST endpoints.

M2 shipped ``POST /sessions`` (provisioning) and ``GET /sessions/{id}``.
M3 adds context selection, file/tree/command workspace endpoints, and the
supervision event timeline.  The agent prompt/patch endpoints live in
``agent/router.py``.

All endpoints require a signed-in user (``require_auth``) and enforce
ownership of the session row (403 when accessing another user's session).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_auth
from app.config import get_settings
from app.db.session import get_db
from app.missions.cache import cached_manifests, detail_extras_for
from app.missions.service import get_mission as get_mission_row
from app.models.command_run import CommandRun
from app.models.file_change import FileChange
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.observability import give_up_blocked_total, give_ups_total, mission_retries_total
from app.sandbox.driver import InvalidRegexError, SearchTimeoutError
from app.schemas.auth import WsTokenRead
from app.schemas.mission import MissionDetail
from app.schemas.session import (
    ContextSelection,
    SessionCreate,
    SessionDetail,
    SessionRead,
    SessionResetResponse,
)
from app.schemas.submission import SubmissionRead
from app.schemas.workspace import (
    MAX_FILE_PATH,
    MAX_STDIO_BYTES,
    CommandBody,
    CommandRunResponse,
    FileContent,
    FileListResponse,
    FileRevertBody,
    FileTreeNodeSchema,
    FileWriteBody,
    SearchMatch,
    SearchRequest,
    SearchResponse,
    SupervisionEventRead,
    UnifiedDiff,
    _validate_workspace_path,
)
from app.sessions.events import EventEmitter, get_redis
from app.sessions.service import (
    ActiveSessionExistsError,
    MissionNotFoundError,
    create_session,
    get_session,
)
from app.sessions.submit import submit_session
from app.ws.auth import issue_ws_token

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _serialize_session(row: SessionRow, settings: Any) -> dict[str, Any]:
    """Serialise a SessionRow into a plain dict, attaching the sandbox driver."""
    data = SessionRead.model_validate(row).model_dump()
    data["sandbox_driver"] = settings.sandbox_driver
    return data


def _load_mission_manifest_extras(mission_id: str) -> dict[str, Any]:
    """Use the shared (cached) manifest cache to enrich a mission detail."""
    loaded = cached_manifests().get(mission_id)
    if loaded is None:
        return {}
    return detail_extras_for(loaded)


def _get_sandbox_handle(request: Request, session_id: uuid.UUID) -> Any:
    """Retrieve the active sandbox handle or raise 503."""
    pool = request.app.state.sandbox_pool
    handle = pool.handle_for(session_id) if hasattr(pool, "handle_for") else None
    if handle is None:
        # Legacy fallback for any pool stub that lacks ``handle_for``.
        for h in pool.handles_snapshot():
            if h.session_id == session_id:
                handle = h
                break
    if handle is None:
        raise HTTPException(
            status_code=503,
            detail="sandbox not provisioned for this session yet",
        )
    return handle


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


async def _require_owned_session(db: AsyncSession, session_id: uuid.UUID, user: User) -> SessionRow:
    """Fetch a session and enforce that ``user`` owns it.

    Raises 404 when the session does not exist (do not leak existence to
    unauthorised callers via a 403) and 403 when it exists but belongs to
    someone else.
    """
    row = await get_session(db, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if row.user_id != user.id:
        raise HTTPException(status_code=403, detail="not your session")
    return row


def _require_mutable_session(row: SessionRow) -> None:
    """Reject mutating workspace ops when the session is no longer active.

    The sandbox-handle 503 covers the case where the pool has already
    destroyed the sandbox, but during the brief ``submitting`` window the
    handle still exists and a stray ``file.edited`` / ``command.run`` /
    ``apply_patch`` would mutate the workspace mid-grade, producing a
    Submission whose ``final_diff`` no longer matches the on-disk repo.
    Tightening to ``active`` only also blocks no-op writes on ``graded`` /
    ``abandoned`` / ``error`` sessions whose handle hasn't been GC'd yet.
    """
    if row.status != "active":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "session_not_active",
                "message": f"session is {row.status!s} — workspace is read-only",
                "session_status": row.status,
            },
        )


# ---------------------------------------------------------------------------
# M2 endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=SessionRead, status_code=202, summary="Create a session")
async def post_session(
    body: SessionCreate,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRead:
    request.state.user = user
    try:
        row = await create_session(
            db,
            user_id=user.id,
            mission_id=body.mission_id,
            previous_session_id=body.previous_session_id,
            mode=body.mode,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"mission not found: {exc}") from exc
    except ActiveSessionExistsError as exc:
        # §21 — per-user concurrency cap (MVP: 1 live session per user).
        # The FE's Resume CTA narrows ``error.body.detail`` and looks for an
        # ``active_session_id`` key, so we ship the conflict metadata in the
        # JSON body. (The historical string-detail-plus-headers shape made
        # the narrowing fall through and the user saw a generic "HTTP 409".)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "active_session_exists",
                "message": "an active session already exists",
                "active_session_id": str(exc.active_session_id),
            },
            headers={
                # Kept for legacy callers / log scraping. Body is the
                # contract; headers are advisory.
                "X-Code": "active_session_exists",
                "X-Active-Session-Id": str(exc.active_session_id),
            },
        ) from exc

    from app.workers.provision import enqueue_provision

    enqueue_provision(row.id)

    # P0-3 observability — count Retry-CTA invocations distinctly so the
    # SLO dashboard can compute retry-rate per mission without
    # disaggregating the broader ``sessions`` counter. Gated on the
    # caller passing a previous_session_id AND that pointer being
    # accepted by create_session (it silently drops stale pointers, so
    # we read row.previous_session_id, not the request body).
    if row.previous_session_id is not None:
        mission_retries_total.labels(mission_id=row.mission_id).inc()
        logger.info(
            "[retry] new session created from prior attempt",
            user_id=str(user.id),
            mission_id=row.mission_id,
            previous_session_id=str(row.previous_session_id),
            attempt_index=row.attempt_index,
            new_session_id=str(row.id),
        )

    return SessionRead.model_validate(_serialize_session(row, get_settings()))


@router.get("/{session_id}", response_model=SessionDetail, summary="Read a session")
async def get_session_endpoint(
    session_id: uuid.UUID,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionDetail:
    row = await _require_owned_session(db, session_id, user)
    settings = get_settings()
    base = _serialize_session(row, settings)

    mission_row = await get_mission_row(db, row.mission_id)
    if mission_row is None:
        # A 500 used to fire here, which framed an unrecoverable data-integrity
        # error to the FE and triggered the global "something went wrong"
        # banner. In practice this branch only fires when the mission row was
        # deleted out from under an active session — the session itself still
        # exists, but the FE has nothing useful to render. 404 is the honest
        # status code and lets the FE route to the missions index gracefully
        # (P1-B2).
        raise HTTPException(status_code=404, detail="Mission not found for session")
    mission_dict = MissionDetail.model_validate(mission_row).model_dump()
    mission_dict.update(_load_mission_manifest_extras(row.mission_id))
    base["mission"] = MissionDetail.model_validate(mission_dict)
    base["ws_token"] = issue_ws_token(
        str(row.id),
        user_id=str(user.id),
        epoch=int(getattr(user, "session_epoch", 1) or 1),
    )
    return SessionDetail.model_validate(base)


@router.get(
    "/{session_id}/ws-token",
    response_model=WsTokenRead,
    summary="Mint a short-lived WS auth token",
)
async def get_ws_token(
    session_id: uuid.UUID,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> WsTokenRead:
    await _require_owned_session(db, session_id, user)
    return WsTokenRead(
        token=issue_ws_token(
            str(session_id),
            user_id=str(user.id),
            epoch=int(getattr(user, "session_epoch", 1) or 1),
        ),
        ttl_seconds=60,
    )


# ---------------------------------------------------------------------------
# M3: Context selection
# ---------------------------------------------------------------------------


@router.post("/{session_id}/context", status_code=204, summary="Update session context selection")
async def post_context(
    session_id: uuid.UUID,
    body: ContextSelection,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    # ``context.selected`` is one of the events the grader keys off for the
    # context_selection dimension. A queued POST that lands AFTER
    # ``submission.requested`` would change the operative-selection result
    # and silently corrupt scoring — same hazard as the other mutating
    # endpoints, so apply the same guard.
    _require_mutable_session(row)

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="context.selected",
        payload={
            "files": body.files,
            "logs": body.logs,
            "tests": body.tests,
            "extras": body.extras,
        },
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# M3: File tree / read / write / revert
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/tree",
    response_model=list[FileTreeNodeSchema],
    summary="List the sandbox file tree",
)
async def get_tree(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> list[FileTreeNodeSchema]:
    await _require_owned_session(db, session_id, user)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool
    root = await pool.driver.list_tree(handle, "/workspace")
    # Return the root's children as a flat list — frontend expects FileTreeNode[].
    root_schema = FileTreeNodeSchema.from_sandbox_node(root)
    return root_schema.children


@router.get(
    "/{session_id}/file", response_model=FileContent, summary="Read a file from the sandbox"
)
async def get_file(
    session_id: uuid.UUID,
    request: Request,
    path: str = Query(
        ...,
        description="Workspace-relative path to the file",
        min_length=1,
        max_length=MAX_FILE_PATH,
    ),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> FileContent:
    await _require_owned_session(db, session_id, user)

    try:
        safe_path = _validate_workspace_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool
    try:
        raw: bytes = await pool.driver.read_file(handle, safe_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    except Exception as exc:
        logger.opt(exception=True).warning("read_file failed for {}: {}", safe_path, exc)
        raise HTTPException(status_code=500, detail="could not read file") from exc

    # Honour the schema's binary-safe contract — files that decode cleanly
    # as UTF-8 ride the fast path; everything else returns base64 so the FE
    # can decide what to do without seeing replacement glyphs.
    try:
        text = raw.decode("utf-8")
        return FileContent(path=safe_path, content=text, encoding="utf-8")
    except UnicodeDecodeError:
        import base64

        return FileContent(
            path=safe_path,
            content=base64.b64encode(raw).decode("ascii"),
            encoding="base64",
        )


@router.post("/{session_id}/files", status_code=204, summary="Write a file in the sandbox")
async def post_file(
    session_id: uuid.UUID,
    body: FileWriteBody,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    _require_mutable_session(row)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

    # body.path has already passed the workspace-path validator at the
    # schema layer (FileWriteBody._check_path). No second validation needed.

    # Read the old content to compute line diff stats. A missing file is
    # the expected case for new-file creation; any other failure is logged
    # but doesn't block the write.
    old_content = ""
    try:
        old_raw: bytes = await pool.driver.read_file(handle, body.path)
        old_content = old_raw.decode("utf-8", errors="replace")
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.opt(exception=True).warning(
            "read_file for diff stats failed for {}: {}", body.path, exc
        )

    new_content = body.content
    old_lines = _count_lines(old_content)
    new_lines = _count_lines(new_content)
    added_lines = max(0, new_lines - old_lines)
    removed_lines = max(0, old_lines - new_lines)

    await pool.driver.write_file(handle, body.path, new_content.encode("utf-8"))
    # The quick-open palette caches the file listing per sandbox; a new file
    # (or a delete-then-rewrite) must invalidate the cached paths so the FE
    # next quick-open immediately sees it.
    _FILES_LIST_CACHE.pop(handle.id, None)

    # Insert FileChange row.
    change = FileChange(
        session_id=session_id,
        path=body.path,
        source="user",
        hunk_count=1,
        added_lines=added_lines,
        removed_lines=removed_lines,
    )
    db.add(change)
    await db.flush()

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="file.edited",
        payload={
            "path": body.path,
            "added": added_lines,
            "removed": removed_lines,
            "source": "user",
        },
    )
    return Response(status_code=204)


@router.post(
    "/{session_id}/files/revert", status_code=204, summary="Revert a file to its initial state"
)
async def post_revert(
    session_id: uuid.UUID,
    body: FileRevertBody,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    _require_mutable_session(row)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

    result = await pool.driver.run(
        handle,
        cmd=["git", "checkout", "--", body.path],
        timeout_s=15,
        cwd="/workspace",
    )
    if result.exit_code != 0:
        logger.warning(
            "git checkout failed for {} (exit={}): stderr={!r}",
            body.path,
            result.exit_code,
            result.stderr[:300],
        )
        raise HTTPException(status_code=500, detail="git checkout failed")
    # A revert removes a file from the working tree (if it was new) or
    # restores its prior content — either way the quick-open listing may
    # change. Drop the cache so the next palette open re-scans.
    _FILES_LIST_CACHE.pop(handle.id, None)

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="file.reverted",
        payload={"path": body.path},
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# P0-12: Reset-to-initial / clean session restart
# ---------------------------------------------------------------------------


def _count_porcelain_lines(porcelain: str) -> int:
    """Count modified + untracked files from ``git status --porcelain`` output."""
    return sum(1 for line in porcelain.splitlines() if line.strip())


async def _had_agent_patch_for_session(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """True when at least one ``patch.applied`` event exists for this session.

    The reset payload uses this to populate ``had_agent_patch`` — a useful
    signal for the post-mortem narrative (a reset without ever applying
    a patch is a different "got lost" pattern than one after a misfire).
    """
    row = (
        await db.execute(
            select(SupervisionEvent.id)
            .where(
                SupervisionEvent.session_id == session_id,
                SupervisionEvent.event_type == "patch.applied",
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _count_session_resets(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Count ``session.reset`` events on the session (post-emit total)."""
    rows = (
        await db.execute(
            select(SupervisionEvent.id).where(
                SupervisionEvent.session_id == session_id,
                SupervisionEvent.event_type == "session.reset",
            )
        )
    ).all()
    return len(rows)


@router.post(
    "/{session_id}/reset",
    response_model=SessionResetResponse,
    summary="Reset the workspace to the mission's initial commit (P0-12)",
)
async def post_reset(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResetResponse:
    """Roll the sandbox back to ``mission.initial_commit``.

    Preconditions:
      * the caller owns the session,
      * ``session.status == 'active'`` (the mutability gate),
      * the sandbox handle is alive (the existing 503 path).

    Side effects (in order):
      1. ``git status --porcelain`` → count discarded files (telemetry).
      2. ``git reset --hard <initial_commit>``.
      3. ``git clean -fd`` — drops untracked files + directories.
      4. Emit ``session.reset`` with
         ``{files_discarded, had_agent_patch, seconds_into_session}``.
      5. Insert a ``FileChange(source='revert', path='*')`` so the file-
         change audit trail records the wipe.

    Concurrency note: the existing apply-patch path holds no per-handle
    lock today (see ``app/sandbox/pool.py``). A reset issued while a
    patch is mid-apply may race; the workspace store is single-tab so
    the FE side rarely produces this. A future hardening pass should
    add a per-handle ``asyncio.Lock`` and wrap both apply-patch and
    reset in it.
    """
    from datetime import UTC, datetime

    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    _require_mutable_session(row)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

    # Resolve the mission's initial commit. Prefer the catalog row (one
    # source of truth seeded by the loader); fall back to the manifest
    # cache for any path that loaded the mission inline.
    mission_row = await get_mission_row(db, row.mission_id)
    initial_commit = (
        getattr(mission_row, "initial_commit", None) if mission_row is not None else None
    )
    if not initial_commit:
        loaded = cached_manifests().get(row.mission_id)
        if loaded is not None:
            initial_commit = getattr(loaded.manifest.repo, "initial_commit", None)
    if not initial_commit:
        # SEV2 — the mission row should always carry an initial_commit
        # (the loader's UPSERT is keyed by mission_id). Refuse to reset
        # to an unknown ref rather than silently shell out to HEAD.
        logger.error(
            "[reset] mission {} has no initial_commit; refusing to reset",
            row.mission_id,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "mission_initial_commit_missing",
                "message": "mission base commit not resolvable",
            },
        )

    # 1) count files about to be discarded — telemetry only, never fatal.
    files_discarded = 0
    try:
        status_result = await pool.driver.run(
            handle,
            cmd=["git", "status", "--porcelain"],
            timeout_s=10,
            cwd="/workspace",
        )
        if status_result.exit_code == 0:
            files_discarded = _count_porcelain_lines(status_result.stdout or "")
    except Exception as exc:  # pragma: no cover — telemetry only
        logger.debug("[reset] git status failed (continuing): {}", exc)

    # 2) hard reset to the pinned commit.
    reset_result = await pool.driver.run(
        handle,
        cmd=["git", "reset", "--hard", initial_commit],
        timeout_s=30,
        cwd="/workspace",
    )
    if reset_result.exit_code != 0:
        logger.error(
            "[reset] git reset --hard {} failed (exit={}): stderr={!r}",
            initial_commit,
            reset_result.exit_code,
            (reset_result.stderr or "")[:300],
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "git_reset_failed",
                "message": "git reset failed; the mission base commit may be unreachable",
            },
        )

    # 3) drop untracked files + directories. The driver returns a RunResult
    # rather than raising on non-zero, so a permission-denied (e.g. a
    # write-protected node_modules tree) would otherwise silently leave
    # orphan untracked files behind while the endpoint reports success.
    # Mirror the ``git reset --hard`` failure handling above.
    try:
        clean_result = await pool.driver.run(
            handle,
            cmd=["git", "clean", "-fd"],
            timeout_s=15,
            cwd="/workspace",
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.error("[reset] git clean -fd raised: {}", exc)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "git_clean_failed",
                "message": "git clean failed; untracked files may remain in the workspace",
            },
        ) from exc
    if clean_result.exit_code != 0:
        logger.error(
            "[reset] git clean -fd failed (exit={}): stderr={!r}",
            clean_result.exit_code,
            (clean_result.stderr or "")[:300],
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "git_clean_failed",
                "message": (clean_result.stderr or "git clean failed").strip()[:300],
            },
        )

    # The reset wipes untracked files and reverts modifications. Drop any
    # cached quick-open listing so the next palette open reflects the
    # post-reset state.
    _FILES_LIST_CACHE.pop(handle.id, None)

    # 4) emit the typed event.
    now = datetime.now(UTC)
    started_at = row.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    seconds_into_session = int((now - started_at).total_seconds())
    had_agent_patch = await _had_agent_patch_for_session(db, session_id)

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="session.reset",
        payload={
            "files_discarded": files_discarded,
            "had_agent_patch": had_agent_patch,
            "seconds_into_session": seconds_into_session,
        },
    )

    # 5) record the wipe in the file_changes audit trail. A single row
    # with path='*' is the deliberate convention for "reset event" so a
    # downstream reader can collapse the run without per-file noise.
    db.add(
        FileChange(
            session_id=session_id,
            path="*",
            source="revert",
            hunk_count=files_discarded,
            added_lines=0,
            removed_lines=0,
        )
    )
    await db.flush()

    reset_count = await _count_session_resets(db, session_id)

    logger.info(
        "[reset] session={} files_discarded={} reset_count={}",
        session_id,
        files_discarded,
        reset_count,
    )

    return SessionResetResponse(
        files_reset=files_discarded,
        new_head_commit=initial_commit,
        reset_count=reset_count,
    )


# ---------------------------------------------------------------------------
# M3: Run commands
# ---------------------------------------------------------------------------

_ALLOWED_CATEGORIES = {"test", "typecheck", "lint", "manual", "other"}


@router.post(
    "/{session_id}/commands",
    response_model=CommandRunResponse,
    summary="Run a command in the sandbox",
)
async def post_command(
    session_id: uuid.UUID,
    body: CommandBody,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> CommandRunResponse:
    request.state.user = user
    if body.category not in _ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {sorted(_ALLOWED_CATEGORIES)}",
        )

    row = await _require_owned_session(db, session_id, user)
    _require_mutable_session(row)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

    result = await pool.driver.run(
        handle,
        cmd=["sh", "-c", body.command],
        timeout_s=120,
        cwd="/workspace",
    )

    cmd_row = CommandRun(
        session_id=session_id,
        command=body.command,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        category=body.category,
    )
    db.add(cmd_row)
    await db.flush()

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="command.run",
        payload={
            "command": body.command,
            "category": body.category,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
        },
    )

    # Truncate large stdio at the API boundary so a single noisy run can't
    # blow up the response and cripple the FE state store.
    truncated_stdout = result.stdout[-MAX_STDIO_BYTES:] if result.stdout else ""
    truncated_stderr = result.stderr[-MAX_STDIO_BYTES:] if result.stderr else ""
    stdio_truncated = (
        len(result.stdout or "") > MAX_STDIO_BYTES or len(result.stderr or "") > MAX_STDIO_BYTES
    )

    return CommandRunResponse(
        id=str(cmd_row.id),
        session_id=str(session_id),
        command=body.command,
        category=body.category,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        created_at=cmd_row.created_at,
        stdout=truncated_stdout,
        stderr=truncated_stderr,
        stdio_truncated=stdio_truncated,
    )


# ---------------------------------------------------------------------------
# M3: Diff and timeline
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/diff",
    response_model=UnifiedDiff,
    summary="Get the workspace diff from initial state",
)
async def get_diff(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> UnifiedDiff:
    await _require_owned_session(db, session_id, user)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool
    diff_text: str = await pool.driver.diff_from_initial(handle)
    return UnifiedDiff(unified_diff=diff_text)


class DiffOpenedBody(BaseModel):
    """Optional body for ``/events/diff-opened`` — surfaces which file was opened."""

    path: str = ""


@router.post(
    "/{session_id}/events/diff-opened",
    status_code=204,
    summary="Record that the user opened the diff viewer (for scoring + badges)",
)
async def post_diff_opened(
    session_id: uuid.UUID,
    request: Request,
    body: DiffOpenedBody | None = None,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Emits a ``diff.opened`` supervision event.

    Driven by the frontend DiffViewer the first time it mounts with a
    non-empty diff. The score engine's "Agent Output Review" dimension
    (§11.2.4) checks for this event after ``patch.applied`` to award up to
    +6 points; without it, the dimension is hard-capped.

    Accepts an optional ``{"path": "..."}`` body so the timeline can name the
    specific file the user opened. An empty body is still accepted for
    backwards compatibility with older clients.
    """
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    # ``diff.opened`` after the grader's own ``submission.requested`` would
    # let a user retroactively inflate their agent_review-dimension score
    # (the dwell-time signal keys off the last diff.opened position). Lock
    # this endpoint down the same way the other mutating ops are locked.
    _require_mutable_session(row)

    path = body.path if body is not None else ""

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="diff.opened",
        payload={"path": path, "surface": "workspace"},
    )
    return Response(status_code=204)


class TutorialStepBody(BaseModel):
    """Body for ``POST /sessions/{id}/events/tutorial-step``.

    ``action`` discriminates "I completed step X" from "I dismissed step X"
    — both write into the supervision event log so the post-session content
    tuning can see exactly which steps users skipped vs. completed. The
    grader ignores tutorial events when scoring.
    """

    step_id: str = Field(min_length=1, max_length=64)
    action: Literal["completed", "dismissed"] = "completed"


@router.post(
    "/{session_id}/events/tutorial-step",
    status_code=204,
    summary="Record a tutorial coachmark step transition (P0-1)",
)
async def post_tutorial_step(
    session_id: uuid.UUID,
    body: TutorialStepBody,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Persist a ``tutorial.step_completed`` or ``tutorial.dismissed`` event.

    These events are tutorial-only — the grader ignores them (Mission 00
    short-circuits the scoring path entirely). Persisting them via the
    supervision-event log keeps the audit trail uniform with every other
    user action, which is the load-bearing invariant for the post-mortem
    replay tool.
    """
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    _require_mutable_session(row)

    event_type = "tutorial.step_completed" if body.action == "completed" else "tutorial.dismissed"
    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type=event_type,
        payload={
            "step_id": body.step_id,
            "mission_id": row.mission_id,
        },
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# P0-9: Find-in-files / repo-wide search
# ---------------------------------------------------------------------------

# Hard upper bound on a single ``GET /files/list`` call. The default is 2000
# (more than enough for any mission repo pack today) and clients pass an
# explicit ``max`` parameter when they want fewer. The cap is enforced even
# when the client requests more so a misconfigured FE can't trigger a 5MB
# JSON payload.
_FILES_LIST_HARD_CAP = 5000

# Cache TTL for the file listing. ``git ls-files`` against a 5k-file workspace
# is ~50ms; the FE's quick-open palette debounces at 120ms so a 30s window
# lets the listing-pop on every keystroke without re-shelling on every char.
# The cache is sandbox-keyed, so a write/revert in one session invalidates
# only that session's entry.
_FILES_LIST_CACHE_TTL_S = 30.0


# Per-process listing cache: ``{sandbox_id: (expires_at_monotonic, paths)}``.
# Module-level so it survives across requests; cleared opportunistically when
# entries expire. The cache is a soft optimization — a stale entry is
# acceptable because the next listing call refreshes it.
_FILES_LIST_CACHE: dict[str, tuple[float, list[str]]] = {}


def _cache_get_paths(sandbox_id: str) -> list[str] | None:
    """Return cached path listing or ``None`` if missing/expired."""
    import time

    entry = _FILES_LIST_CACHE.get(sandbox_id)
    if entry is None:
        return None
    expires_at, paths = entry
    if expires_at < time.monotonic():
        _FILES_LIST_CACHE.pop(sandbox_id, None)
        return None
    return paths


def _cache_put_paths(sandbox_id: str, paths: list[str]) -> None:
    """Store a listing in the per-process cache with a TTL."""
    import time

    _FILES_LIST_CACHE[sandbox_id] = (
        time.monotonic() + _FILES_LIST_CACHE_TTL_S,
        paths,
    )


@router.get(
    "/{session_id}/files/list",
    response_model=FileListResponse,
    summary="List workspace files (gitignore-aware, fuzzy-filtered server-side)",
)
async def get_files_list(
    session_id: uuid.UUID,
    request: Request,
    query: str | None = Query(
        default=None,
        description="Optional substring filter (case-insensitive). Applied AFTER the listing fetch.",
        max_length=200,
    ),
    max: int = Query(
        default=2000,
        ge=1,
        le=_FILES_LIST_HARD_CAP,
        alias="max",
        description="Maximum number of paths to return (hard cap 5000).",
    ),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> FileListResponse:
    """Return workspace paths for the quick-open file palette.

    The listing comes from ``git ls-files --cached --others
    --exclude-standard`` so ``.gitignore`` is honoured and untracked-but-not-
    ignored files surface immediately. Results are deduplicated and sorted by
    depth-then-name (top-level entrypoints first), then filtered by ``query``
    if supplied. The substring match is intentionally simple — the full fuzzy
    score lives on the client where it can re-rank without a roundtrip.

    Cached per sandbox for 30 seconds so a fast-typing user doesn't shell out
    to ``git`` on every keystroke. The cache is bypassed transparently when
    the sandbox is destroyed (the handle id changes).
    """
    await _require_owned_session(db, session_id, user)
    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

    cached = _cache_get_paths(handle.id)
    if cached is None:
        cached = await pool.driver.list_files(handle, max_files=_FILES_LIST_HARD_CAP)
        _cache_put_paths(handle.id, cached)

    paths = cached
    total = len(paths)
    if query:
        needle = query.lower()
        paths = [p for p in paths if needle in p.lower()]

    truncated = len(paths) > max
    if truncated:
        paths = paths[:max]

    return FileListResponse(paths=paths, truncated=truncated, total=total)


@router.post(
    "/{session_id}/files/search",
    response_model=SearchResponse,
    summary="Find-in-files across the workspace (ripgrep-backed)",
)
async def post_files_search(
    session_id: uuid.UUID,
    body: SearchRequest,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Run a ripgrep-backed search across the sandbox workspace.

    The search itself is read-only — but we DO emit a ``command.run``
    supervision event with ``category='manual'`` so the grader's
    context_selection dimension can credit the supervisor for actually
    poking around the workspace before prompting. The event payload
    carries the query (truncated) and the result count; the literal
    ripgrep argv is never logged because it could contain sensitive
    user-supplied substrings.

    Errors:
        400 ``invalid_regex`` — the user enabled regex mode and the pattern
            failed to compile.
        504 ``search_timeout`` — the ripgrep subprocess exceeded the 10s
            wall-clock budget.
    """
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    # Search is read-only on the workspace, but we still gate on ``active``:
    # an in-flight submit means the diff is being frozen, and a stray search
    # event mid-grade would muddy the supervision log without buying anything.
    _require_mutable_session(row)

    import time as _time

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

    from app.observability import workspace_search_total

    started_ms = _time.monotonic()
    try:
        raw_matches, truncated, total, exit_code = await pool.driver.search(
            handle,
            body.query,
            glob=body.glob,
            case_sensitive=body.case_sensitive,
            regex=body.regex,
            max_results=body.max_results,
        )
    except InvalidRegexError as exc:
        workspace_search_total.labels(outcome="invalid_regex").inc()
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_regex",
                "message": str(exc) or "regex failed to compile",
            },
        ) from exc
    except SearchTimeoutError as exc:
        workspace_search_total.labels(outcome="timeout").inc()
        raise HTTPException(
            status_code=504,
            detail={
                "code": "search_timeout",
                "message": str(exc) or "search timed out",
            },
        ) from exc
    duration_ms = int((_time.monotonic() - started_ms) * 1000)

    matches: list[SearchMatch] = [SearchMatch.model_validate(m) for m in raw_matches]

    # Persist a ``command.run`` event so the grader's "supervisor used
    # find-in-files" signal lands in the timeline. The event keeps the same
    # shape as a real shell-invoked search so downstream readers don't have
    # to special-case the surface.
    #
    # Phase 4.A.19 — surface the real ripgrep exit code (was hardcoded to
    # 0) AND emit a ``validator.flag{kind="search_error"}`` when the
    # exit code is non-zero non-empty. ripgrep's rc=1 legitimately means
    # "no matches" so we only flag on rc=2 (pattern/IO/glob error).
    cmd_label = f"search:{body.query[:120]}"
    cmd_row = CommandRun(
        session_id=session_id,
        command=cmd_label,
        exit_code=int(exit_code),
        duration_ms=duration_ms,
        category="manual",
    )
    db.add(cmd_row)
    await db.flush()

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="command.run",
        payload={
            "command": cmd_label,
            "category": "manual",
            "exit_code": int(exit_code),
            "duration_ms": duration_ms,
            "surface": "find_in_files",
            "result_count": total,
        },
    )

    if exit_code not in (0, 1):
        # rc=2 is the only non-empty non-zero ripgrep code today — emit
        # a search_error validator flag so the timeline surfaces the
        # underlying failure (otherwise the user sees "no results" and
        # has no signal that ripgrep itself broke).
        await emitter.emit(
            session_id=session_id,
            event_type="validator.flag",
            payload={
                "kind": "search_error",
                "message": f"workspace search exited rc={int(exit_code)}",
                "penalty": 0,
            },
        )

    if truncated:
        workspace_search_total.labels(outcome="truncated").inc()
    else:
        workspace_search_total.labels(outcome="ok").inc()

    return SearchResponse(
        matches=matches,
        truncated=truncated,
        total=total,
        duration_ms=duration_ms,
    )


@router.get(
    "/{session_id}/timeline",
    response_model=list[SupervisionEventRead],
    summary="Return the supervision event timeline for the session",
)
async def get_timeline(
    session_id: uuid.UUID,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SupervisionEventRead]:
    await _require_owned_session(db, session_id, user)

    stmt = (
        select(SupervisionEvent)
        .where(SupervisionEvent.session_id == session_id)
        .order_by(SupervisionEvent.occurred_at, SupervisionEvent.id)
    )
    events = list((await db.execute(stmt)).scalars().all())
    return [SupervisionEventRead.model_validate(ev) for ev in events]


# ---------------------------------------------------------------------------
# M5: Submit + Grading
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/submit",
    response_model=SubmissionRead,
    summary="Submit a session for grading",
)
async def post_submit(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SubmissionRead:
    """Trigger the grading pipeline for a session.

    Returns 200 with the final ``SubmissionRead`` once grading completes
    (the call blocks until the runner returns; see ``sessions/submit.py``).
    """
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    return await submit_session(db=db, session=row, request=request)


# ---------------------------------------------------------------------------
# P0-4 — give up & reveal
# ---------------------------------------------------------------------------


# Soft-block window before "Give up" is allowed (ADR 0010). Sourced from
# Settings.give_up_min_seconds so ops can adjust without redeploying. Kept
# in seconds; a future per-mission override (``mission.give_up_after_seconds``)
# would extend this lookup rather than replace the global default. Tests
# read the value via ``get_settings().give_up_min_seconds`` so they don't
# hardcode the constant either.


@router.post(
    "/{session_id}/give-up",
    response_model=SubmissionRead,
    summary="Forfeit the session, reveal the ideal solution, cap the score at 50",
)
async def post_give_up(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SubmissionRead:
    """Forfeit the active session and immediately submit it for grading.

    Preconditions:
      * The caller owns the session.
      * ``session.status == 'active'`` (a session that's already submitting
        or graded can't be given up on — the user must wait for the
        in-flight grade to finish, then they can retry).
      * At least :data:`GIVE_UP_MIN_SECONDS` (10 min) have elapsed since
        ``session.started_at`` — prevents quitting before engaging.

    Side-effects (in order):
      1. Emit ``session.gave_up`` supervision event with the
         ``seconds_into_session`` payload (so the timeline reflects the
         deliberate forfeit).
      2. Stamp ``sessions.gave_up_at = now()``. The grading runner reads
         this flag when computing the score report and applies a 50/100
         cap with ``score_cap_reason='gave_up'``.
      3. Call the standard submit pipeline (same path as
         ``POST /sessions/{id}/submit``).

    Errors:
      * 409 — session is not active (already graded, abandoned, errored,
        or mid-submit). Detail includes the current status.
      * 425 (Too Early) — 10-min window hasn't elapsed yet. Detail
        carries ``seconds_remaining`` so the FE can render a countdown.
    """
    from datetime import UTC, datetime

    from app.sessions.events import EventEmitter, get_redis

    request.state.user = user
    row = await _require_owned_session(db, session_id, user)

    if row.status != "active":
        # Mirror the same 409 envelope shape the FE already knows how to
        # render (see the ``session_not_active`` code used by the workspace
        # mutators above).
        raise HTTPException(
            status_code=409,
            detail={
                "code": "session_not_active",
                "message": (f"session is {row.status!s} — give-up requires an active session"),
                "session_status": row.status,
            },
        )

    # P0-4 audit fix — tutorial missions short-circuit the grader and never
    # persist a Submission row. Allowing give-up there would set
    # ``gave_up_at`` but no submission would ever carry ``score_cap_reason``,
    # producing an orphaned timestamp the profile aggregator + report page
    # can't render coherently. Reject up-front; tutorials are a "learn the
    # dojo" surface, not a give-up surface.
    mission_row = await get_mission_row(db, row.mission_id)
    if mission_row is not None and getattr(mission_row, "kind", "standard") == "tutorial":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "give_up_not_supported_for_tutorial",
                "message": (
                    "give-up isn't available on the orientation tutorial — just complete or skip it"
                ),
                "session_status": row.status,
            },
        )

    now = datetime.now(UTC)
    started_at = row.started_at
    # The DB column is TIMESTAMPTZ but SQLite-backed tests sometimes return a
    # naive datetime. Normalise to UTC-aware so the subtraction is honest.
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    seconds_elapsed = int((now - started_at).total_seconds())
    gate_seconds = get_settings().give_up_min_seconds
    if seconds_elapsed < gate_seconds:
        seconds_remaining = gate_seconds - seconds_elapsed
        # P0-4 observability — count gate hits so ops can spot users
        # hammering the affordance and consider lowering the gate.
        give_up_blocked_total.labels(mission_id=row.mission_id).inc()
        logger.info(
            "[give_up] blocked by 10-min gate",
            session_id=str(session_id),
            user_id=str(user.id),
            mission_id=row.mission_id,
            seconds_elapsed=seconds_elapsed,
            seconds_remaining=seconds_remaining,
        )
        # 425 Too Early signals "the request is valid but the server isn't
        # ready to accept it yet" — matches the "the gate isn't open"
        # semantics better than 400/409. The FE's GiveUpDialog narrows on
        # the ``code`` key and renders the countdown.
        raise HTTPException(
            status_code=425,
            detail={
                "code": "give_up_not_yet_available",
                "message": ("give-up requires at least 10 minutes in session"),
                "seconds_remaining": seconds_remaining,
                "seconds_required": gate_seconds,
            },
            headers={"Retry-After": str(seconds_remaining)},
        )

    # Persist the give-up event BEFORE flipping ``gave_up_at`` so a crash
    # between the emit and the column write doesn't leave the session
    # capped but without an event in the timeline. The event itself is
    # consumed by the timeline; the column is consumed by the grading
    # runner — they're independent contracts, but ordering matters for
    # forensics.
    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="session.gave_up",
        payload={
            "seconds_into_session": seconds_elapsed,
            "started_at_iso": started_at.isoformat(),
            "gave_up_at_iso": now.isoformat(),
        },
    )

    row.gave_up_at = now
    await db.flush()
    # Commit BEFORE submit_session — the submit path issues its own atomic
    # claim UPDATE which would lose this flush if a downstream error
    # rolled back the transaction.
    await db.commit()
    await db.refresh(row)

    logger.info(
        "[give_up] forfeit accepted",
        session_id=str(session_id),
        user_id=str(user.id),
        mission_id=row.mission_id,
        seconds_into_session=seconds_elapsed,
    )

    # Hand off to the standard submit pipeline. The runner reads
    # ``session.gave_up_at`` and applies the cap inside ``_pipeline``.
    submission = await submit_session(db=db, session=row, request=request)
    # P0-4 observability — record the give-up outcome. ``cap_applied`` is
    # the binary signal: did the user's honest score exceed 50, so the
    # cap was binding (true), or was it under 50 already (false)? Both
    # cases still record the deliberate forfeit; the dimension distinguishes
    # "stopped a strong attempt" from "stopped a weak attempt".
    uncapped: int = submission.total_score
    if isinstance(submission.score_report, dict):
        candidate = submission.score_report.get("uncapped_total")
        if isinstance(candidate, int):
            uncapped = candidate
    cap_was_binding = submission.total_score < uncapped
    give_ups_total.labels(
        mission_id=row.mission_id,
        cap_applied="true" if cap_was_binding else "false",
    ).inc()
    return submission


@router.get(
    "/{session_id}/submission",
    response_model=SubmissionRead,
    summary="Retrieve the grading result for a session",
)
async def get_submission(
    session_id: uuid.UUID,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SubmissionRead:
    """Return the grading submission for an already-graded session.

    Returns 404 if the session has not been submitted yet, or 404 if the
    session itself does not exist. ``ideal_solution`` /
    ``ideal_solution_diff`` / ``agent_patch_diff`` are injected from disk
    when ``session.status == 'graded'`` so the FE can render the
    post-mortem walkthrough (P0-2) without a second roundtrip to
    ``/reports``.
    """
    session_row = await _require_owned_session(db, session_id, user)

    submission = (
        await db.execute(select(Submission).where(Submission.session_id == session_id))
    ).scalar_one_or_none()
    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="no submission found for this session — has it been submitted?",
        )
    # Pull the P0-2 supplementary diffs from disk. We import inline so
    # this module's import graph doesn't grow a hard dependency on the
    # reports module.
    from app.reports.router import (
        _read_agent_patch_diff,
        _read_ideal_solution,
        _read_ideal_solution_diff,
        _to_read_model,
    )

    settings = get_settings()
    return _to_read_model(
        submission,
        _read_ideal_solution(settings.missions_root, session_row.mission_id),
        session_row.status,
        ideal_solution_diff=_read_ideal_solution_diff(
            settings.missions_root, session_row.mission_id
        ),
        agent_patch_diff=_read_agent_patch_diff(settings.missions_root, session_row.mission_id),
        mission_id=session_row.mission_id,
    )
