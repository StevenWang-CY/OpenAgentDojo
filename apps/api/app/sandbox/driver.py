"""Abstract sandbox driver contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.sandbox.types import (
    ApplyResult,
    FileTreeNode,
    GradingArtifacts,
    RunResult,
    SandboxHandle,
)


class SandboxDriver(ABC):
    """All drivers implement the same async surface (plan §9.2)."""

    name: str = "abstract"

    @abstractmethod
    async def provision(self, mission: Any, session_id: Any) -> SandboxHandle: ...

    @abstractmethod
    async def attach_shell(self, handle: SandboxHandle) -> Any:
        """Return a (reader, writer, close_callable) tuple for a PTY stream."""

    @abstractmethod
    async def read_file(self, handle: SandboxHandle, path: str) -> bytes: ...

    @abstractmethod
    async def write_file(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    @abstractmethod
    async def list_tree(self, handle: SandboxHandle, root: str = "/workspace") -> FileTreeNode: ...

    @abstractmethod
    async def diff_from_initial(self, handle: SandboxHandle) -> str: ...

    @abstractmethod
    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_s: int = 60,
        cwd: str | None = None,
    ) -> RunResult: ...

    @abstractmethod
    async def apply_diff(self, handle: SandboxHandle, diff_text: str) -> ApplyResult: ...

    @abstractmethod
    async def freeze_and_grade(
        self,
        handle: SandboxHandle,
        mission: Any,
        *,
        manifest_folder: Path | None = None,
    ) -> GradingArtifacts: ...

    @abstractmethod
    async def destroy(self, handle: SandboxHandle) -> None: ...
