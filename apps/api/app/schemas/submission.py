"""Submission read schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SubmissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    final_diff: str = ""
    total_score: int
    visible_test_results: dict[str, Any] = Field(default_factory=dict)
    hidden_test_results: dict[str, Any] = Field(default_factory=dict)
    validator_results: dict[str, Any] = Field(default_factory=dict)
    score_report: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Injected at read-time by GET /reports/{id}; not persisted to the DB.
    ideal_solution: str | None = None
