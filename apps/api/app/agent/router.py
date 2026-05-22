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
from functools import lru_cache
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


@lru_cache(maxsize=1)
def get_agent_service() -> AgentService:
    """Return the process-wide :class:`AgentService` instance.

    Wrapped in :func:`functools.lru_cache` so FastAPI's ``Depends`` resolves
    to the same object on every call (cheap O(1) hash lookup) without us
    having to maintain a module-level singleton that runs at import time —
    which would build the LLM client during ``pytest`` collection.

    Tests that need to swap the LLM out use::

        from app.agent.router import get_agent_service
        get_agent_service.cache_clear()
        # …or use FastAPI's app.dependency_overrides[get_agent_service] = ...
    """
    return AgentService()


# Back-compat alias for callers that imported the old module-level singleton.
# Resolves lazily so ``import app.agent.router`` is still side-effect-free.
def __getattr__(name: str) -> Any:  # pragma: no cover — import shim
    if name == "agent_service":
        return get_agent_service()
    raise AttributeError(name)


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class PromptBody(BaseModel):
    """POST body for ``/sessions/{id}/prompts``.

    Bounded at 20k characters — anything beyond is pathological for a
    supervision prompt and would balloon DB rows + WS event payloads.
    """

    text: str = Field(min_length=1, max_length=20_000)
    context: ContextSelection | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_sandbox_handle(request: Request, session_id: uuid.UUID) -> Any:
    """Return the active sandbox handle or raise 503.

    Mirrors ``sessions/router._get_sandbox_handle`` — prefer the O(1)
    ``handle_for`` lookup and fall back to the linear ``handles_snapshot``
    scan for any legacy pool stub (e.g. test doubles) that doesn't implement
    the indexed accessor.
    """
    pool = request.app.state.sandbox_pool
    handle = pool.handle_for(session_id) if hasattr(pool, "handle_for") else None
    if handle is None:
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
    agent_service: AgentService = Depends(get_agent_service),
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
    detector_error: str | None = None
    try:
        matched = detect_prompt_injection(body.text)
    except Exception as exc:
        # Detector is pure regex so a raise here is unusual and almost always
        # indicates a regression in the pattern table (a broken backref, etc).
        # Bumped from DEBUG to WARNING so the operator sees the traceback in
        # the structured log (P1-B6); without this the failure was effectively
        # invisible. ``logger.opt(exception=True)`` attaches the traceback —
        # loguru ignores the stdlib ``exc_info`` kwarg, so calling it the
        # idiomatic way is required for the operator to see the stack frame.
        # We still surface a supervision event so graders/replays know safety
        # analysis was skipped for this turn — the payload carries BOTH
        # ``kind`` (legacy contract still consumed by the scorer + FE) and
        # ``reason`` (new canonical field per the rubric §11.2.6 safety
        # dimension).
        logger.opt(exception=True).warning(
            "prompt-injection detector raised — safety analysis skipped: {}",
            exc,
        )
        matched = []
        detector_error = str(exc)[:200]
    if detector_error is not None:
        try:
            await emitter.emit(
                session_id=session.id,
                event_type="validator.flag",
                payload={
                    "kind": "prompt_injection_detector_error",
                    "reason": "prompt_injection_check_failed",
                    "message": detector_error,
                },
            )
        except Exception as exc:  # pragma: no cover — telemetry must never block
            logger.warning(
                "prompt-injection detector-error validator.flag emit failed: {}",
                exc,
            )
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
    summary="Apply the agent patch for a specific turn (no request body)",
)
async def post_apply_patch(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_auth),
    agent_service: AgentService = Depends(get_agent_service),
) -> PatchResult:
    """Apply the agent-proposed patch for ``turn_id``.

    Takes no request body — the (turn_id, session_id) tuple in the URL is
    the only input. The previous ``ApplyPatchBody`` placeholder model was
    removed (P1-B9): nothing consumed it and publishing an empty schema as
    part of the public OpenAPI surface led FE generators to mint a useless
    type alias.
    """
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
    #
    # P0: previously this block swallowed ``HTTPException`` and silently fell
    # back to ``loaded=None`` — which made ``apply_patch`` ignore the
    # manifest's configured ``agent.patch_file`` override and apply the
    # historical ``agent_patch.diff`` instead. A cache miss here is a real
    # bug (the missions cache should be seeded by the time a session is
    # alive), so we log loudly and surface the 500 to the caller rather
    # than apply the wrong patch.
    try:
        loaded = _resolve_manifest(session.mission_id)
    except HTTPException:
        logger.exception(
            "[agent] manifest cache miss for mission {} during apply_patch — "
            "refusing to apply blind",
            session.mission_id,
        )
        raise

    return await agent_service.apply_patch(
        db=db,
        session=session,
        turn_id=turn_id,
        sandbox_driver=pool.driver,
        sandbox_handle=handle,
        emitter=emitter,
        manifest=loaded,
    )
