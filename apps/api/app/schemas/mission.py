"""Public mission schemas — what the catalog endpoints return."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

Difficulty = Literal["beginner", "intermediate", "advanced"]
MissionCategory = Literal[
    "auth",
    "testing",
    "security",
    "frontend",
    "api",
    "database",
    "refactoring",
    "agent-safety",
    "review",
    "debugging",
]


class MissionListItem(BaseModel):
    """Minimal mission card payload for `GET /missions`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    short_description: str = ""
    difficulty: Difficulty
    category: MissionCategory
    estimated_minutes: int
    failure_mode_id: str = Field(validation_alias=AliasChoices("failure_mode_id", "failure_mode"))
    skills_tested: list[str] = Field(default_factory=list)
    version: int = 1
    published: bool = True


class MissionDetail(BaseModel):
    """`GET /missions/{id}` payload.

    Withholds ``ideal_solution`` and any hidden-test surface; brief is included
    because the workspace needs it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    short_description: str = ""
    difficulty: Difficulty
    category: MissionCategory
    estimated_minutes: int
    failure_mode_id: str = Field(validation_alias=AliasChoices("failure_mode_id", "failure_mode"))
    skills_tested: list[str] = Field(default_factory=list)
    repo_pack: str
    initial_commit: str
    manifest_sha256: str
    version: int = 1
    published: bool = True
    brief: str = ""
    language_runtime: Literal["node20", "python312"] | None = None
    visible_tests: list[str] = Field(default_factory=list)
    expected_context_required: list[str] = Field(default_factory=list)
    expected_context_recommended: list[str] = Field(default_factory=list)
    expected_diff_lines_p50: int | None = None
