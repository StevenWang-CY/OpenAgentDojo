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


# ---------------------------------------------------------------------------
# P0-9 — Find-in-files / repo-wide search
# ---------------------------------------------------------------------------

# Caps applied to search inputs. The ripgrep subprocess inherits ``--max-count``
# and ``--max-columns`` from these so a malicious query can't blow up the API
# memory footprint or the FE result list.
MAX_SEARCH_QUERY = 200
MAX_SEARCH_GLOB = 200
MAX_SEARCH_RESULTS = 1000
DEFAULT_SEARCH_RESULTS = 200
MAX_SEARCH_LINE_TEXT = 500


class FileListResponse(BaseModel):
    """``GET /sessions/{id}/files/list`` response body.

    ``paths`` are repo-relative (no leading ``/workspace`` prefix) and sorted
    deterministically (depth then name) so the FE can paginate / fuzzy-filter
    client-side without re-sorting on every keystroke. ``truncated`` is true
    when the underlying listing exceeded the per-call cap; callers should
    surface a "// some files omitted" hint.
    """

    paths: list[str] = Field(default_factory=list)
    truncated: bool = False
    total: int = 0


class SearchRequest(BaseModel):
    """Body for ``POST /sessions/{id}/files/search``.

    ``query`` is the literal substring or PCRE pattern fed to ripgrep.
    ``glob`` is an optional ripgrep ``--glob`` argument (e.g. ``src/**/*.ts``);
    the leading/trailing whitespace is stripped at the schema layer.
    ``regex`` enables PCRE handling; otherwise the query is treated as a
    fixed-string. ``max_results`` is clamped to ``[1, 1000]`` so a runaway
    query can't dump the entire workspace into the API response.
    """

    query: str = Field(min_length=1, max_length=MAX_SEARCH_QUERY)
    glob: str | None = Field(default=None, max_length=MAX_SEARCH_GLOB)
    case_sensitive: bool = False
    regex: bool = False
    max_results: int = Field(default=DEFAULT_SEARCH_RESULTS, ge=1, le=MAX_SEARCH_RESULTS)

    @field_validator("query")
    @classmethod
    def _check_query(cls, value: str) -> str:
        if not value or value.isspace():
            raise ValueError("query must not be empty")
        if "\x00" in value:
            raise ValueError("query must not contain NUL bytes")
        return value

    @field_validator("glob")
    @classmethod
    def _check_glob(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if "\x00" in cleaned:
            raise ValueError("glob must not contain NUL bytes")
        return cleaned


class SearchMatch(BaseModel):
    """A single ripgrep hit, normalised for the FE.

    ``path`` is workspace-relative (no ``/workspace`` prefix). ``line_text`` is
    truncated to :data:`MAX_SEARCH_LINE_TEXT` chars so a single match on a
    minified file can't blow up the response. ``match_start``/``match_end``
    are byte offsets within the (possibly truncated) ``line_text``.
    """

    path: str
    line_number: int = Field(ge=1)
    line_text: str
    match_start: int = Field(ge=0)
    match_end: int = Field(ge=0)


class SearchResponse(BaseModel):
    """``POST /sessions/{id}/files/search`` response body."""

    matches: list[SearchMatch] = Field(default_factory=list)
    truncated: bool = False
    total: int = 0
    duration_ms: int = 0


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
