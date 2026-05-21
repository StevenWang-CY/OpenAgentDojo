"""Agent turn schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.session import ContextSelection


class AgentTurnResponse(BaseModel):
    """One agent reply, including any actions the user can perform on it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    turn_index: int
    user_prompt: str
    selected_context: ContextSelection
    agent_response: str
    proposed_actions: list[str] = Field(default_factory=list)
    applied_patch: str | None = None
    patch_applied_at: datetime | None = None
    created_at: datetime


class PatchResult(BaseModel):
    applied: bool
    files_changed: list[str] = Field(default_factory=list)
    added_lines: int = 0
    removed_lines: int = 0
    error: str | None = None
