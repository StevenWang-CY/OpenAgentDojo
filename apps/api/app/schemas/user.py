"""User-facing schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    handle: str | None = None
    display_name: str | None = None
    github_login: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None
    # ``/auth/me`` always sets a CSRF token (either the existing cookie or a
    # freshly minted one — see ``_build_me_response``). The schema reflects
    # that: making it required prevents FE call sites from defensively
    # branching on a value that the BE guarantees.
    csrf_token: str
    # P0-1 — when set, the user has finished Mission 00 at least once. The
    # catalog's "// start here" banner renders ONLY when this is null; the
    # "Replay tutorial" entry in the header dropdown re-clears this server-
    # side and bumps ``tutorial_replay_count``.
    tutorial_completed_at: datetime | None = None
    # P0-1 — incremented every time the user re-runs the tutorial. Surfaced
    # to the FE so the "// orientation · completed YYYY-MM-DD · replay" row
    # can show "(replayed Nx)" once the count is non-zero. Internal-only
    # telemetry that never makes it to public profile surfaces.
    tutorial_replay_count: int = 0
