"""Supervision event schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SupervisionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: uuid.UUID
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime
