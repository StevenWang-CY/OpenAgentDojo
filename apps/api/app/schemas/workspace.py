"""Workspace-operation request/response schemas (file ops, commands, diffs)."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field

CommandCategory = Literal["test", "typecheck", "lint", "manual", "other"]


class FileWriteBody(BaseModel):
    path: str
    content: str


class FileRevertBody(BaseModel):
    path: str


class CommandBody(BaseModel):
    command: str
    category: CommandCategory = "other"


FileEncoding = Literal["utf-8", "base64"]


class FileContent(BaseModel):
    """`GET /sessions/{id}/file` response body.

    The frontend type mirror lives in
    ``packages/shared-types/src/api.ts`` and is generated from this model
    via ``openapi-typescript``. The binary-safe ``base64`` encoding is
    declared up-front so future binary reads do not require a schema
    migration.
    """

    path: str = ""
    content: str
    encoding: FileEncoding = "utf-8"
    truncated: bool = False


class UnifiedDiff(BaseModel):
    unified_diff: str


class CommandRunResponse(BaseModel):
    """Response returned after running a command in the sandbox."""

    id: str
    session_id: str
    command: str
    category: str = "other"
    exit_code: int | None = None
    duration_ms: int | None = None
    created_at: str = ""
    stdout: str = ""
    stderr: str = ""


class SupervisionEventRead(BaseModel):
    """A single supervision event from the timeline."""

    id: int
    session_id: str
    event_type: str
    payload: dict = Field(default_factory=dict)
    occurred_at: str


class FileTreeNodeSchema(BaseModel):
    """Frontend-compatible file-tree node.

    Converts the sandbox ``FileTreeNode`` dataclass, which uses ``kind="dir"``,
    into the shape the frontend expects: ``kind="directory"``, plus a ``name``
    field derived from the path basename.
    """

    path: str
    name: str
    kind: Literal["file", "directory"]
    size: int | None = None
    children: list[FileTreeNodeSchema] = Field(default_factory=list)

    @classmethod
    def from_sandbox_node(cls, node: Any) -> FileTreeNodeSchema:
        name = PurePosixPath(node.path).name or node.path
        kind: Literal["file", "directory"] = (
            "directory" if node.kind in ("dir", "directory") else "file"
        )
        children = [cls.from_sandbox_node(c) for c in (node.children or [])]
        return cls(
            path=node.path,
            name=name,
            kind=kind,
            size=None if kind == "directory" else (node.size or None),
            children=children,
        )
