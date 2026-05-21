"""Agent endpoints — prompt submission and patch application (plan §8, M4).

Endpoints:
  POST /sessions/{session_id}/prompts
      Submit a user prompt; returns AgentTurnResponse.

  POST /sessions/{session_id}/patches/{turn_id}/apply
      Apply the agent-generated patch for a specific turn; returns PatchResult.

Both routes require an authenticated caller (``require_auth``) and enforce
ownership: the caller's ``user_id`` MUST equal ``session.user_id`` or we
return ``403``. Agent 4.1 is rolling out auth broadly across the API, but
these routes pin auth + ownership explicitly so the agent path can't
regress in isolation.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.injection import detect_prompt_injection
from app.agent.service import AgentService, _find_mission_folder
from app.auth.deps import require_auth
from app.config import get_settings
from app.db.session import get_db
from app.missions.cache import cached_manifests
from app.models.user import User
from app.schemas.agent_turn import AgentTurnResponse, PatchResult
from app.schemas.session import ContextSelection
from app.sessions.events import EventEmitter, get_redis
from app.sessions.service import get_session

router = APIRouter(prefix="/sessions", tags=["agent"])

# Module-level singleton — constructed lazily-internally, no LLM build on import.
agent_service = AgentService()


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class PromptBody(BaseModel):
    """POST body for ``/sessions/{id}/prompts``."""

    text: str = Field(min_length=1)
    context: ContextSelection | None = None


class ApplyPatchBody(BaseModel):
    """POST body for ``/sessions/{id}/patches/{turn_id}/apply``.

    Currently empty; reserved so the endpoint can accept driver overrides
    (e.g. dry-run) without a breaking-change later.
    """

    dry_run: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_sandbox_handle(request: Request, session_id: uuid.UUID) -> Any:
    pool = request.app.state.sandbox_pool
    for h in pool.handles_snapshot():
        if h.session_id == session_id:
            return h
    raise HTTPException(
        status_code=503,
        detail="sandbox not provisioned for this session yet",
    )


def _resolve_mission_folder(mission_id: str) -> Path:
    settings = get_settings()
    folder = _find_mission_folder(mission_id, settings.missions_root)
    if folder is None:
        raise HTTPException(
            status_code=500,
            detail=f"mission folder for '{mission_id}' not found on disk",
        )
    return folder


def _resolve_manifest(mission_id: str) -> Any:
    loaded = cached_manifests().get(mission_id)
    if loaded is None:
        raise HTTPException(
            status_code=500,
            detail=f"mission manifest for '{mission_id}' not in cache — reseed required",
        )
    return loaded


def _enforce_ownership(session: Any, user: User) -> None:
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="forbidden")


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/prompts
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/prompts",
    response_model=AgentTurnResponse,
    summary="Submit a prompt and get an agent response",
)
async def post_prompt(
    session_id: uuid.UUID,
    body: PromptBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_auth),
) -> AgentTurnResponse:
    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    _enforce_ownership(session, user)

    context = body.context or ContextSelection()
    mission_folder = _resolve_mission_folder(session.mission_id)
    loaded = _resolve_manifest(session.mission_id)

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)

    # M8 §21: detect prompt-injection patterns and flag (do NOT block) so the
    # post-hoc safety scorer can pick up the signal. Best-effort — a failure
    # here must never break the prompt path.
    try:
        matched = detect_prompt_injection(body.text)
    except Exception as exc:  # pragma: no cover — detector is pure regex
        logger.debug("prompt-injection detector raised: {}", exc)
        matched = []
    if matched:
        try:
            await emitter.emit(
                session_id=session.id,
                event_type="validator.flag",
                payload={
                    "kind": "prompt_injection",
                    "patterns": matched,
                    "message": ("Prompt-injection patterns detected; flagged, not blocked"),
                },
            )
        except Exception as exc:  # pragma: no cover — telemetry must never block
            logger.warning("prompt-injection validator.flag emit failed: {}", exc)

    return await agent_service.respond(
        db=db,
        session=session,
        prompt=body.text,
        context=context,
        mission_folder=mission_folder,
        manifest=loaded,
        emitter=emitter,
    )


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/patches/{turn_id}/apply
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/patches/{turn_id}/apply",
    response_model=PatchResult,
    summary="Apply the agent patch for a specific turn",
)
async def post_apply_patch(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    request: Request,
    body: ApplyPatchBody | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_auth),
) -> PatchResult:
    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    _enforce_ownership(session, user)

    handle = _get_sandbox_handle(request, session_id)
    pool = request.app.state.sandbox_pool

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)

    # Lookup the manifest so apply_patch can pick up an optional
    # ``agent.patch_file`` override declared by the mission.
    loaded = None
    try:
        loaded = _resolve_manifest(session.mission_id)
    except HTTPException:
        loaded = None

    return await agent_service.apply_patch(
        db=db,
        session=session,
        turn_id=turn_id,
        sandbox_driver=pool.driver,
        sandbox_handle=handle,
        emitter=emitter,
        manifest=loaded,
    )
