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
    # Lists per the shared-types contract (each entry is a TestRunResult
    # or ValidatorResult dict). Legacy rows persisted as ``dict[suite_name,
    # TestRunResult]`` are still accepted via the ``list | dict`` union so a
    # mid-deploy read of an older row doesn't 500. The unionised pydantic
    # validator returns the on-disk shape as-is.
    visible_test_results: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    hidden_test_results: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    validator_results: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    score_report: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Injected at read-time by GET /reports/{id}; not persisted to the DB.
    ideal_solution: str | None = None
