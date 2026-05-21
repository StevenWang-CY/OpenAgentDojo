"""Plain dataclasses used across sandbox drivers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SandboxHandle:
    """Identifies a provisioned sandbox.

    Drivers attach implementation-specific state via :attr:`driver_state`.
    """

    id: str
    driver: str  # "docker" | "local"
    workdir: Path
    mission_id: str
    session_id: uuid.UUID
    container_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    driver_state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    command: str = ""


@dataclass(slots=True)
class FileTreeNode:
    path: str
    kind: str  # "file" | "dir"
    size: int = 0
    children: list[FileTreeNode] = field(default_factory=list)


@dataclass(slots=True)
class ApplyResult:
    applied: bool
    files_changed: list[str] = field(default_factory=list)
    added_lines: int = 0
    removed_lines: int = 0
    error: str | None = None


@dataclass(slots=True)
class GradingArtifacts:
    diff: str
    test_results: dict[str, Any]
    logs: dict[str, str]
