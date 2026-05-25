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
    # P0-3 — when a user clicks "Retry this mission" on the report page, the
    # FE passes the prior submission's ``session_id`` so the new session
    # links back to the chain. Optional; absent for first attempts and
    # for the catalog "Start mission" CTA. Validated against ownership at
    # the service layer.
    previous_session_id: uuid.UUID | None = None


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
    # P0-3 — 1-based ordinal of this attempt against (user_id, mission_id).
    # Always >= 1; surfaced so the workspace shell can render "Attempt N"
    # in the header without a second roundtrip.
    attempt_index: int = 1
    # P0-3 — back-pointer set when the session was created via "Retry".
    # NULL means "this was a first attempt or a fresh start from the
    # catalog" — the chain is for traceability, not gating.
    previous_session_id: uuid.UUID | None = None
    # P0-4 — when set, the user invoked the give-up affordance. The grading
    # path applies a 50/100 cap and stamps submission.score_cap_reason.
    # Surfacing this on the session read lets the FE render the gave-up
    # chip on the workspace shell before navigating to the report.
    gave_up_at: datetime | None = None


class SessionDetail(SessionRead):
    """`GET /sessions/{id}` response — adds workspace bootstrap material."""

    mission: MissionDetail
    ws_token: str


class SessionResetResponse(BaseModel):
    """`POST /sessions/{id}/reset` response (P0-12).

    Carries the commit HEAD now points to (== mission's initial_commit,
    since the workspace is now byte-identical to provision-time), the
    count of files the reset discarded (telemetry — read by the FE store
    to size the toast), and the running count of reset events on this
    session.
    """

    files_reset: int = Field(ge=0)
    new_head_commit: str
    reset_count: int = Field(ge=1)
