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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_auth
from app.config import get_settings
from app.db.session import get_db
from app.missions.router import _cached_manifests, _detail_extras_for
from app.missions.service import get_mission as get_mission_row
from app.models.command_run import CommandRun
from app.models.file_change import FileChange
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.schemas.mission import MissionDetail
from app.schemas.session import ContextSelection, SessionCreate, SessionDetail, SessionRead
from app.schemas.submission import SubmissionRead
from app.schemas.workspace import (
    CommandBody,
    CommandRunResponse,
    FileContent,
    FileRevertBody,
    FileTreeNodeSchema,
    FileWriteBody,
    SupervisionEventRead,
    UnifiedDiff,
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
    loaded = _cached_manifests().get(mission_id)
    if loaded is None:
        return {}
    return _detail_extras_for(loaded)


def _get_sandbox_handle(request: Request, session_id: uuid.UUID) -> Any:
    """Retrieve the active sandbox handle or raise 503."""
    pool = request.app.state.sandbox_pool
    for h in pool.handles_snapshot():
        if h.session_id == session_id:
            return h
    raise HTTPException(
        status_code=503,
        detail="sandbox not provisioned for this session yet",
    )


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
        row = await create_session(db, user_id=user.id, mission_id=body.mission_id)
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"mission not found: {exc}") from exc
    except ActiveSessionExistsError as exc:
        # §21 — per-user concurrency cap (MVP: 1 live session per user).
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "an active session already exists",
                "code": "active_session_exists",
                "active_session_id": str(exc.active_session_id),
            },
        ) from exc

    from app.workers.provision import enqueue_provision

    enqueue_provision(row.id)

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
        raise HTTPException(status_code=500, detail="session's mission missing")
    mission_dict = MissionDetail.model_validate(mission_row).model_dump()
    mission_dict.update(_load_mission_manifest_extras(row.mission_id))
    base["mission"] = MissionDetail.model_validate(mission_dict)
    base["ws_token"] = issue_ws_token(str(row.id))
    return SessionDetail.model_validate(base)


@router.get("/{session_id}/ws-token", summary="Mint a short-lived WS auth token")
async def get_ws_token(
    session_id: uuid.UUID,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    await _require_owned_session(db, session_id, user)
    return {"token": issue_ws_token(str(session_id)), "ttl_seconds": 60}


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
    await _require_owned_session(db, session_id, user)

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
    path: str = Query(..., description="Absolute or workspace-relative path to the file"),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> FileContent:
    await _require_owned_session(db, session_id, user)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool
    try:
        raw: bytes = await pool.driver.read_file(handle, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"file not found: {path}") from exc
    except Exception as exc:
        logger.warning("read_file failed for {}: {}", path, exc)
        raise HTTPException(status_code=500, detail=f"could not read file: {exc}") from exc

    return FileContent(path=path, content=raw.decode("utf-8", errors="replace"))


@router.post("/{session_id}/files", status_code=204, summary="Write a file in the sandbox")
async def post_file(
    session_id: uuid.UUID,
    body: FileWriteBody,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    request.state.user = user
    await _require_owned_session(db, session_id, user)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

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
        logger.warning("read_file for diff stats failed for {}: {}", body.path, exc)

    new_content = body.content
    old_lines = _count_lines(old_content)
    new_lines = _count_lines(new_content)
    added_lines = max(0, new_lines - old_lines)
    removed_lines = max(0, old_lines - new_lines)

    await pool.driver.write_file(handle, body.path, new_content.encode("utf-8"))

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
    await _require_owned_session(db, session_id, user)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

    result = await pool.driver.run(
        handle,
        cmd=["git", "checkout", "--", body.path],
        timeout_s=15,
        cwd="/workspace",
    )
    if result.exit_code != 0:
        raise HTTPException(
            status_code=500,
            detail=f"git checkout failed: {result.stderr[:300]}",
        )

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="file.reverted",
        payload={"path": body.path},
    )
    return Response(status_code=204)


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

    await _require_owned_session(db, session_id, user)

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

    return CommandRunResponse(
        id=str(cmd_row.id),
        session_id=str(session_id),
        command=body.command,
        category=body.category,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        created_at=cmd_row.created_at.isoformat(),
        stdout=result.stdout,
        stderr=result.stderr,
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
    await _require_owned_session(db, session_id, user)

    path = body.path if body is not None else ""

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="diff.opened",
        payload={"path": path, "surface": "workspace"},
    )
    return Response(status_code=204)


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
        .order_by(SupervisionEvent.occurred_at)
    )
    events = list((await db.execute(stmt)).scalars().all())
    return [
        SupervisionEventRead(
            id=ev.id,
            session_id=str(ev.session_id),
            event_type=ev.event_type,
            payload=ev.payload,
            occurred_at=ev.occurred_at.isoformat(),
        )
        for ev in events
    ]


# ---------------------------------------------------------------------------
# M5: Submit + Grading
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/submit",
    response_model=SubmissionRead,
    status_code=202,
    summary="Submit a session for grading",
)
async def post_submit(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SubmissionRead:
    """Trigger the grading pipeline for a session.

    Returns 202 Accepted with the ``SubmissionRead`` once grading completes.
    """
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)
    return await submit_session(db=db, session=row, request=request)


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
    session itself does not exist.
    """
    await _require_owned_session(db, session_id, user)

    submission = (
        await db.execute(select(Submission).where(Submission.session_id == session_id))
    ).scalar_one_or_none()
    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="no submission found for this session — has it been submitted?",
        )
    return SubmissionRead.model_validate(submission)
