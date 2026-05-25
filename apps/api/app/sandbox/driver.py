"""Abstract sandbox driver contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypedDict

from app.sandbox.types import (
    ApplyResult,
    FileTreeNode,
    GradingArtifacts,
    RunResult,
    SandboxHandle,
)


class SearchTimeoutError(RuntimeError):
    """Raised when a workspace search exceeds the driver's wall-clock budget.

    The session router maps this to a 504 with ``{"code": "search_timeout"}``
    so the FE can render a friendly "search took too long" toast instead of a
    generic 500.
    """


class InvalidRegexError(ValueError):
    """Raised when a ripgrep query fails to compile.

    The session router maps this to a 400 with ``{"code": "invalid_regex"}``
    plus the underlying error message so the FE can highlight the offending
    pattern.
    """


class SearchMatchDict(TypedDict):
    """Wire shape for :meth:`SandboxDriver.search` matches.

    Mirrors :class:`app.schemas.workspace.SearchMatch` but lives in the driver
    layer so the schema package doesn't have to leak into the docker/local
    drivers. The router converts these dicts into the Pydantic model.
    """

    path: str
    line_number: int
    line_text: str
    match_start: int
    match_end: int


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

    async def list_files(
        self,
        handle: SandboxHandle,
        *,
        max_files: int = 5000,
    ) -> list[str]:
        """Return repo-relative paths honouring ``.gitignore``.

        Drivers shell out to ``git ls-files --cached --others
        --exclude-standard`` so the listing matches what a developer would see
        in a freshly-cloned repo (no ``node_modules``, no ``.next`` cache,
        no untracked-but-ignored noise). ``max_files`` is a hard cap; results
        beyond it are dropped and the caller is expected to flag truncation
        via the ``FileListResponse.truncated`` field.

        The base implementation returns an empty list so drivers that don't
        own a real filesystem (e.g. test fakes, the orphan-sweep null driver)
        don't need to provide a stub. Real drivers (local, docker) override.
        """
        _ = handle, max_files
        return []

    async def search(
        self,
        handle: SandboxHandle,
        query: str,
        *,
        glob: str | None,
        case_sensitive: bool,
        regex: bool,
        max_results: int,
    ) -> tuple[list[SearchMatchDict], bool, int, int]:
        """Run a ripgrep search across the sandbox workspace.

        Returns ``(matches, truncated, total, exit_code)``. ``matches``
        is capped at ``max_results`` (which the router clamps to
        ``[1, 1000]``); ``truncated`` reflects whether ripgrep would have
        produced more hits absent the cap. ``total`` is the number of
        matches actually returned (equal to ``len(matches)`` in the
        happy path; useful for the FE so callers don't have to
        recompute). ``exit_code`` is the real ripgrep exit code (Phase
        4.A.19) — surfaced so the router can emit it on the
        ``command.run`` event payload and flag a search-error validator
        signal when non-zero non-empty (``rc=2`` = pattern / IO error;
        ``rc=1`` legitimately means "no matches" so the router does NOT
        flag on it).

        The base implementation returns an empty result, matching the
        same rationale as :meth:`list_files`. Real drivers override.

        Raises:
            SearchTimeoutError: when the ripgrep subprocess exceeds the
                driver's wall-clock budget (10s).
            InvalidRegexError: when ``regex=True`` and the pattern fails to
                compile under PCRE.
        """
        _ = handle, query, glob, case_sensitive, regex, max_results
        return [], False, 0, 0

    @abstractmethod
    async def destroy(self, handle: SandboxHandle) -> None: ...

    async def ping(self) -> bool:
        """Lightweight readiness probe — used by ``/healthz/ready``.

        Drivers override to verify their underlying runtime is reachable
        (e.g. the Docker daemon). The default returns ``True`` so a driver
        that has nothing to probe (the local subprocess driver) stays ready
        without each subclass having to repeat the boilerplate.
        """
        return True
