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
    csrf_token: str | None = None
