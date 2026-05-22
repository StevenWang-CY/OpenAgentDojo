"""Workspace-operation request/response schemas (file ops, commands, diffs)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CommandCategory = Literal["test", "typecheck", "lint", "manual", "other"]

# Hard caps on user-supplied workspace inputs. These are the schema-level
# floors; middleware tightens further on specific routes.
MAX_FILE_PATH = 512
MAX_FILE_CONTENT_BYTES = 5_000_000  # 5 MiB cap to bound API memory + sandbox tmpfs
MAX_COMMAND_LENGTH = 8192
MAX_STDIO_BYTES = 65_536  # truncate command stdio in the API response

# Workspace paths must be relative to /workspace and free of `..` traversal.
_WORKSPACE_PATH_RE = re.compile(r"^[A-Za-z0-9_./\-]+$")


def _validate_workspace_path(value: str) -> str:
    """Reject absolute paths, traversal, and unusual characters."""
    if not value or value.isspace():
        raise ValueError("path must not be empty")
    if "\x00" in value:
        raise ValueError("path must not contain NUL bytes")
    if value.startswith("/") or value.startswith("\\"):
        raise ValueError("path must be workspace-relative, not absolute")
    pure = PurePosixPath(value)
    parts = pure.parts
    if any(p == ".." for p in parts):
        raise ValueError("path must not contain '..' segments")
    if not _WORKSPACE_PATH_RE.match(value):
        raise ValueError(
            "path contains unsupported characters; allowed: letters, digits, '_', '.', '/', '-'"
        )
    return value


class FileWriteBody(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_FILE_PATH)
    content: str = Field(max_length=MAX_FILE_CONTENT_BYTES)

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return _validate_workspace_path(value)


class FileRevertBody(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_FILE_PATH)

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return _validate_workspace_path(value)


class CommandBody(BaseModel):
    command: str = Field(min_length=1, max_length=MAX_COMMAND_LENGTH)
    category: CommandCategory = "other"


FileEncoding = Literal["utf-8", "base64"]


class FileContent(BaseModel):
    """`GET /sessions/{id}/file` response body.

    The frontend type mirror lives in
    ``packages/shared-types/src/api.ts`` and is generated from this model
    via ``openapi-typescript``. ``encoding`` reports whether the file was
    safe to decode as UTF-8 (``"utf-8"``) or had to be returned as
    base64-encoded bytes for binary content (``"base64"``).
    """

    path: str = ""
    content: str
    encoding: FileEncoding = "utf-8"
    truncated: bool = False


class UnifiedDiff(BaseModel):
    unified_diff: str


class CommandRunResponse(BaseModel):
    """Response returned after running a command in the sandbox.

    ``stdout``/``stderr`` are truncated by the route handler to
    :data:`MAX_STDIO_BYTES` and ``stdio_truncated`` is set when the tail was
    trimmed so the FE can show a hint.
    """

    id: str
    session_id: str
    command: str
    category: CommandCategory = "other"
    exit_code: int | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None
    stdout: str = ""
    stderr: str = ""
    stdio_truncated: bool = False


class SupervisionEventRead(BaseModel):
    """A single supervision event from the timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str = Field(..., json_schema_extra={"format": "uuid"})
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime

    @field_validator("session_id", mode="before")
    @classmethod
    def _stringify_session_id(cls, value: Any) -> Any:
        return str(value) if value is not None else value


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
