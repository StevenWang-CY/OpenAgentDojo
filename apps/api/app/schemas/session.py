"""Session schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.mission import MissionDetail

SessionStatus = Literal["provisioning", "active", "submitting", "graded", "abandoned", "error"]

SandboxDriver = Literal["docker", "local"]


class ContextSelection(BaseModel):
    """The set of artifacts the user has selected as relevant for the turn."""

    files: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    extras: list[str] = Field(default_factory=list)


class SessionCreate(BaseModel):
    mission_id: str


class SessionRead(BaseModel):
    """Bare session row — used as the response for `POST /sessions`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    mission_id: str
    status: SessionStatus
    started_at: datetime
    completed_at: datetime | None = None
    sandbox_id: str | None = None
    sandbox_driver: SandboxDriver = "local"
    current_commit: str | None = None
    score: int | None = None
    agent_turns: int = 0


class SessionDetail(SessionRead):
    """`GET /sessions/{id}` response — adds workspace bootstrap material."""

    mission: MissionDetail
    ws_token: str
