"""Local subprocess + git-based sandbox driver.

Used when ``SANDBOX_DRIVER=local``. Every session gets a ``tempfile.mkdtemp``
under ``SANDBOX_WORKDIR``. The repo pack (if present) is copied in and a
``git init`` + initial commit is created so ``diff_from_initial`` works.

This driver is for **development only** — it provides no isolation. The app
logs a loud warning when this driver is active.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import get_settings
from app.sandbox.driver import SandboxDriver
from app.sandbox.repo_pack import RepoPackNotFoundError, load_repo_pack
from app.sandbox.types import (
    ApplyResult,
    FileTreeNode,
    GradingArtifacts,
    RunResult,
    SandboxHandle,
)


class LocalSandboxDriver(SandboxDriver):
    """Subprocess-based driver — dev only."""

    name = "local"

    def __init__(self) -> None:
        settings = get_settings()
        self.root = Path(settings.sandbox_workdir).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ provision
    async def provision(self, mission: Any, session_id: Any) -> SandboxHandle:
        """Create a per-session temp dir, copy pack contents in, git init."""
        sid = uuid.UUID(str(session_id))
        # ``.resolve()`` is load-bearing on macOS — /tmp is a symlink to
        # /private/tmp, and any later call to ``_resolve`` invokes
        # ``Path.resolve()`` which canonicalises the prefix. Without this,
        # the workdir-escape check below fires for ``/workspace`` lookups
        # ("." resolves to /private/tmp/... while handle.workdir was still
        # /tmp/..." — different paths even though they point at the same
        # inode), and the file tree / file-read endpoints all return 500.
        workdir = Path(tempfile.mkdtemp(prefix=f"arena-{sid}-", dir=str(self.root))).resolve()

        repo_pack = self._mission_repo_pack(mission)
        if repo_pack is not None:
            try:
                pack_root = load_repo_pack(repo_pack)
                # symlinks=True is load-bearing: pnpm's node_modules layout uses
                # relative symlinks (backend/node_modules/<dep> → ../../node_modules/
                # .pnpm/<dep>@<ver>/node_modules/<dep>) that resolve *within* the
                # pack root. Default (symlinks=False) dereferences them, which
                # copies fragments of the .pnpm store as real dirs but breaks the
                # inter-package peer links — vitest then fails to resolve `pathe`
                # at runtime. Repo packs MUST be installed standalone (their pack
                # roots are intentionally NOT root-workspace members) so every
                # symlink target lives inside the pack.
                shutil.copytree(
                    pack_root,
                    workdir,
                    dirs_exist_ok=True,
                    symlinks=True,
                    ignore_dangling_symlinks=True,
                )
            except RepoPackNotFoundError:
                # Missing pack is non-fatal for the MVP — the user gets an empty
                # workspace and the test suite can still exercise the driver.
                logger.warning("repo pack {} missing — sandbox provisioned empty", repo_pack)

        await self._git_init_and_commit(workdir, "initial_commit")
        mission_id = self._mission_id(mission) or "unknown"

        logger.info("local sandbox provisioned: workdir={} session={}", workdir, sid)
        return SandboxHandle(
            id=str(uuid.uuid4()),
            driver=self.name,
            workdir=workdir,
            mission_id=mission_id,
            session_id=sid,
        )

    @staticmethod
    def _mission_repo_pack(mission: Any) -> str | None:
        if mission is None:
            return None
        # Manifest-style first; ORM-style second.
        repo = getattr(mission, "repo", None)
        if repo is not None:
            return getattr(repo, "pack", None)
        return getattr(mission, "repo_pack", None)

    @staticmethod
    def _mission_id(mission: Any) -> str | None:
        if mission is None:
            return None
        return getattr(mission, "id", None) or getattr(
            getattr(mission, "manifest", None), "id", None
        )

    async def _git_init_and_commit(self, workdir: Path, message: str) -> None:
        env = self._git_env()
        await _run_subprocess(
            ["git", "init", "--initial-branch=main", "--quiet"], cwd=workdir, env=env
        )
        await _run_subprocess(["git", "add", "-A"], cwd=workdir, env=env)
        # `--allow-empty` so empty workspaces still produce a commit reference.
        await _run_subprocess(
            ["git", "commit", "--allow-empty", "-q", "-m", message],
            cwd=workdir,
            env=env,
        )

    @staticmethod
    def _git_env() -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "OpenAgentDojo Sandbox")
        env.setdefault("GIT_AUTHOR_EMAIL", "sandbox@arena.local")
        env.setdefault("GIT_COMMITTER_NAME", "OpenAgentDojo Sandbox")
        env.setdefault("GIT_COMMITTER_EMAIL", "sandbox@arena.local")
        env.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
        env.setdefault("GIT_CONFIG_SYSTEM", "/dev/null")
        return env

    # ----------------------------------------------------------------- pty
    async def attach_shell(self, handle: SandboxHandle) -> Any:
        """Spawn a bash subprocess attached to a PTY for the WS bridge.

        Each call allocates a *fresh* PTY (so the workspace supports multiple
        terminal tabs simultaneously). The PTY is tracked under a random
        ``ptyid`` inside ``handle.driver_state["ptys"]`` so the WS bridge can
        close just its own PTY on disconnect without disturbing siblings.

        Returns ``(pty_fd, proc, ptyid)``. Legacy two-tuple unpackers that did
        ``fd, proc = await attach_shell(...)`` will fail loudly — that contract
        is incompatible with multi-tab. Callers must read all three values.
        """
        import pty

        primary, secondary = pty.openpty()
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-i",
            cwd=str(handle.workdir),
            stdin=secondary,
            stdout=secondary,
            stderr=secondary,
            env=self._git_env(),
            start_new_session=True,
        )
        os.close(secondary)

        ptys: dict[str, tuple[int, int]] = handle.driver_state.setdefault("ptys", {})
        ptyid = uuid.uuid4().hex
        ptys[ptyid] = (primary, proc.pid)
        # Legacy slots kept as "most-recent" pointers for back-compat with any
        # caller still introspecting them; do not rely on them for cleanup.
        handle.driver_state["pty_fd"] = primary
        handle.driver_state["pty_pid"] = proc.pid
        return primary, proc, ptyid

    def close_pty(self, handle: SandboxHandle, ptyid: str) -> None:
        """Close a single PTY tracked under ``ptyid``; no-op if already gone."""
        ptys: dict[str, tuple[int, int]] = handle.driver_state.get("ptys", {})
        entry = ptys.pop(ptyid, None)
        if entry is None:
            return
        fd, pid = entry
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    # --------------------------------------------------------------- files
    def _resolve(self, handle: SandboxHandle, path: str) -> Path:
        # Strip the "/workspace" prefix if the caller used it (docker idiom).
        if path.startswith("/workspace/"):
            path = path[len("/workspace/") :]
        elif path == "/workspace":
            path = "."
        target = (handle.workdir / path).resolve()
        # Defensive: never escape the workdir.
        if handle.workdir not in target.parents and target != handle.workdir:
            raise PermissionError(f"path escapes workdir: {path}")
        return target

    async def read_file(self, handle: SandboxHandle, path: str) -> bytes:
        target = self._resolve(handle, path)
        # Off the event loop — a large mission file can stall the API
        # otherwise (P1-B24).
        return await asyncio.to_thread(target.read_bytes)

    async def write_file(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        target = self._resolve(handle, path)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        await asyncio.to_thread(_write)

    async def list_tree(self, handle: SandboxHandle, root: str = "/workspace") -> FileTreeNode:
        base = self._resolve(handle, root)
        return _build_tree(base, base)

    async def diff_from_initial(self, handle: SandboxHandle) -> str:
        # `--no-color`, `HEAD` is the initial_commit by construction.
        result = await self.run(handle, ["git", "--no-pager", "diff", "HEAD"], timeout_s=30)
        return result.stdout

    # ------------------------------------------------------------------ run
    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_s: int = 60,
        cwd: str | None = None,
    ) -> RunResult:
        run_cwd = handle.workdir if cwd is None else self._resolve(handle, cwd)
        env = self._git_env()
        started = time.monotonic()
        timed_out = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(run_cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except TimeoutError:
                proc.kill()
                stdout_b, stderr_b = await proc.communicate()
                timed_out = True
            exit_code = proc.returncode if proc.returncode is not None else -1
        except FileNotFoundError as exc:
            return RunResult(
                exit_code=127,
                stdout="",
                stderr=f"command not found: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
                command=" ".join(cmd),
            )

        return RunResult(
            exit_code=exit_code,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
            command=" ".join(cmd),
        )

    # ---------------------------------------------------------------- diff
    async def apply_diff(self, handle: SandboxHandle, diff_text: str) -> ApplyResult:
        # Stage the patch in a per-sandbox subdir so concurrent sessions
        # can't clobber each other's patch files (and so a stray patch
        # never ends up in the shared sandbox-root listing). Write with
        # mode 0o600 — the diff may contain unreviewed model output and
        # has no business being world-readable on the host.
        diff_file = handle.workdir / ".arena_patch.diff"
        diff_bytes = diff_text.encode("utf-8")
        fd = os.open(
            str(diff_file),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, diff_bytes)
        finally:
            os.close(fd)
        try:
            result = await self.run(
                handle,
                ["git", "apply", "--3way", "--whitespace=fix", str(diff_file)],
                timeout_s=30,
            )
        finally:
            diff_file.unlink(missing_ok=True)
        if result.exit_code != 0:
            return ApplyResult(applied=False, error=result.stderr or result.stdout)

        # Re-derive file deltas from the freshly-applied diff for the UI.
        names_result = await self.run(
            handle,
            ["git", "--no-pager", "diff", "--name-only", "HEAD"],
            timeout_s=10,
        )
        files = [f for f in names_result.stdout.splitlines() if f]
        stat_result = await self.run(
            handle,
            ["git", "--no-pager", "diff", "--numstat", "HEAD"],
            timeout_s=10,
        )
        added = removed = 0
        for line in stat_result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    added += int(parts[0]) if parts[0] != "-" else 0
                    removed += int(parts[1]) if parts[1] != "-" else 0
                except ValueError:
                    continue
        return ApplyResult(
            applied=True, files_changed=files, added_lines=added, removed_lines=removed
        )

    async def freeze_and_grade(
        self,
        handle: SandboxHandle,
        mission: Any,
        *,
        manifest_folder: Path | None = None,
    ) -> GradingArtifacts:
        """Real M5 grading: snapshot diff, copy hidden tests, run visible + hidden suites.

        ``manifest_folder`` is the mission directory containing ``mission.yaml``
        and the ``hidden_tests/`` subtree. When omitted (legacy callers), the
        hidden tests phase is skipped and only the visible suites run.
        """
        # --- snapshot diff ---
        diff = await self.diff_from_initial(handle)

        # --- mount hidden tests BEFORE freezing so we can copy in ---
        # Canonical (spec §9.3): /grader/hidden_tests/ — kept OUTSIDE the
        # workspace so the user cannot list, read, or pre-pass them via the
        # /tree, /file, or shell endpoints. Mission runner scripts MUST
        # reference the GRADER_DIR env var passed below, not a workspace-
        # relative path. The previous compat double-mount inside
        # /workspace/hidden_tests/ was an information-disclosure hole.
        hidden_dir = manifest_folder / "hidden_tests" if manifest_folder is not None else None
        if hidden_dir is not None and hidden_dir.is_dir():
            target = handle.workdir / "grader" / "hidden_tests"
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(hidden_dir, target, dirs_exist_ok=True)

        # NOTE: The spec says to mark the workspace read-only before running
        # tests, but real test runners (vitest, jest) need write access to
        # their cache + temp dirs. We defer the chmod-freeze until AFTER
        # tests complete so the artifacts are still snapshot-stable for the
        # grader's report. The local driver has no isolation anyway (it is
        # dev-only — see plan §9.4); the docker driver enforces real
        # read-only mounts.

        # --- visible test phases ---
        test_results: dict[str, Any] = {}
        logs: dict[str, str] = {}

        test_commands = self._test_commands(mission)
        for suite, cmd in test_commands.items():
            tr = await self._run_test_phase(handle, suite, cmd, timeout_s=180)
            test_results[suite] = _test_run_to_dict(tr)
            logs[f"visible.{suite}"] = tr.stdout + "\n--- stderr ---\n" + tr.stderr

        # --- hidden test phase ---
        if hidden_dir is not None and hidden_dir.is_dir():
            hidden_cmd = self._hidden_command(mission)
            # Mission runner scripts often hard-code "/workspace" — point
            # them at the real workdir in the local driver.
            wrapped = (
                f"WORKSPACE_DIR={shlex.quote(str(handle.workdir))} "
                f"GRADER_DIR={shlex.quote(str(handle.workdir / 'grader' / 'hidden_tests'))} "
                f"{hidden_cmd}"
            )
            tr = await self._run_test_phase(handle, "hidden", wrapped, timeout_s=180)
            test_results["hidden"] = _test_run_to_dict(tr)
            logs["hidden"] = tr.stdout + "\n--- stderr ---\n" + tr.stderr
        elif manifest_folder is not None:
            # Manifest folder given but no hidden tests dir — synthesise a fail
            # so the scorer caps final_correctness rather than treating absence
            # as a pass.
            test_results["hidden"] = {
                "suite": "hidden",
                "exit_code": 127,
                "stdout": "",
                "stderr": f"hidden_tests directory not found: {hidden_dir}",
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "timed_out": False,
            }

        return GradingArtifacts(diff=diff, test_results=test_results, logs=logs)

    # --- freeze_and_grade helpers ---

    @staticmethod
    def _test_commands(mission: Any) -> dict[str, str]:
        repo = getattr(mission, "repo", None)
        cmds = getattr(repo, "test_commands", None) if repo is not None else None
        if not cmds:
            return {}
        return dict(cmds)

    @staticmethod
    def _hidden_command(mission: Any) -> str:
        # Default runner lives under the /grader mount (outside the user
        # workspace) so the user cannot pre-pass it. We accept the legacy
        # ``hidden_tests/...`` workspace-relative path in mission YAMLs and
        # rewrite it to the canonical $GRADER_DIR-prefixed form so authors
        # don't need to migrate every mission.yaml at once. The rewrite is
        # word-boundary-aware so commands that already point at the
        # canonical ``grader/hidden_tests/...`` path pass through unchanged.
        default_cmd = 'bash "$GRADER_DIR/runner.sh"'
        hidden = getattr(mission, "hidden_tests", None)
        raw = getattr(hidden, "command", default_cmd) if hidden is not None else default_cmd
        raw = raw or default_cmd
        return _re.sub(r'(?<![\w/])hidden_tests/', '"$GRADER_DIR"/', raw)

    async def _run_test_phase(
        self,
        handle: SandboxHandle,
        suite: str,
        cmd: str,
        timeout_s: int,
    ) -> _TestPhaseResult:
        """Run a single test phase command, parse counts, classify timeout."""
        # For hidden tests, the runner.sh expects cwd at the workspace root
        # (so paths like ./grader/hidden_tests/runner.sh work). For visible
        # suites, the cwd is the workspace root too.
        result = await self.run(handle, ["bash", "-lc", cmd], timeout_s=timeout_s)
        passed, failed, skipped = _parse_test_counts(result.stdout, result.stderr)

        if result.timed_out:
            # Per spec: surface timeout explicitly — runner penalises accordingly.
            return _TestPhaseResult(
                suite=suite,
                exit_code=max(1, result.exit_code),
                stdout=result.stdout,
                stderr=result.stderr + f"\n[test phase '{suite}' timed out after {timeout_s}s]",
                passed=0,
                failed=0,
                skipped=0,
                timed_out=True,
            )

        return _TestPhaseResult(
            suite=suite,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            passed=passed,
            failed=failed,
            skipped=skipped,
            timed_out=False,
        )

    async def destroy(self, handle: SandboxHandle) -> None:
        # Close every PTY this handle owns (multi-tab safe).
        ptys: dict[str, tuple[int, int]] = handle.driver_state.get("ptys", {}) or {}
        for ptyid in list(ptys.keys()):
            self.close_pty(handle, ptyid)

        # Back-compat: drain the legacy single-slot fields if anything ever
        # populated them outside attach_shell.
        pid = handle.driver_state.pop("pty_pid", None)
        if pid:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        fd = handle.driver_state.pop("pty_fd", None)
        if fd:
            try:
                os.close(fd)
            except OSError:
                pass

        # `chmod` back to writable so rmtree works even after freeze.
        # As with freeze_and_grade: never follow symlinks (they may target
        # files OUTSIDE the workdir).
        try:
            for p in handle.workdir.rglob("*"):
                if p.is_symlink():
                    continue
                try:
                    p.chmod(0o755 if p.is_dir() else 0o644)
                except (PermissionError, FileNotFoundError, OSError):
                    continue
        except OSError:
            pass
        shutil.rmtree(handle.workdir, ignore_errors=True)


# --------------------------------------------------------------- helpers


_TREE_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".pnpm",
        ".pnpm-store",
        ".venv",
        "__pycache__",
        ".next",
        "dist",
        "build",
        ".turbo",
    }
)


def _build_tree(base: Path, root: Path) -> FileTreeNode:
    # Use lstat-aware checks so we never descend INTO a symlink (pnpm's
    # backend/node_modules/<dep> symlinks resolve to dirs inside the pack;
    # following them would explode the tree from ~50 files into ~50_000).
    is_symlink = base.is_symlink()
    if base.is_dir() and not is_symlink:
        node = FileTreeNode(
            path=str(base.relative_to(root)) or ".",
            kind="dir",
        )
        try:
            for child in sorted(base.iterdir()):
                if child.name in _TREE_SKIP_DIRS:
                    continue
                node.children.append(_build_tree(child, root))
        except PermissionError:
            pass
        return node
    # File OR symlink — report as file so the workspace UI can list it; we
    # avoid following the symlink for `st_size` because the target may
    # legitimately be missing (dangling) or live outside the workdir.
    try:
        size = base.lstat().st_size
    except (FileNotFoundError, OSError):
        size = 0
    return FileTreeNode(
        path=str(base.relative_to(root)),
        kind="file",
        size=size,
    )


async def _run_subprocess(
    args: list[str], cwd: Path, env: dict[str, str], timeout_s: int = 30
) -> subprocess.CompletedProcess[bytes]:
    """Internal helper: run a subprocess and raise on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        proc.kill()
        raise RuntimeError(f"subprocess timed out: {' '.join(args)}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args)} exited {proc.returncode}: {stderr.decode(errors='replace').strip()}"
        )
    return subprocess.CompletedProcess(args, proc.returncode or 0, stdout, stderr)


# ---------------------------------------------------------------------------
# Test-phase parsing helpers (used by freeze_and_grade)
# ---------------------------------------------------------------------------


import json as _json  # noqa: E402
import re as _re  # noqa: E402
from dataclasses import dataclass as _dataclass  # noqa: E402


@_dataclass(slots=True)
class _TestPhaseResult:
    suite: str
    exit_code: int
    stdout: str
    stderr: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    timed_out: bool = False


def _test_run_to_dict(result: _TestPhaseResult) -> dict[str, Any]:
    return {
        "suite": result.suite,
        "exit_code": result.exit_code,
        "stdout": result.stdout[-8000:],  # cap for JSONB size
        "stderr": result.stderr[-4000:],
        "passed": result.passed,
        "failed": result.failed,
        "skipped": result.skipped,
        "timed_out": result.timed_out,
    }


def _parse_test_counts(stdout: str, stderr: str) -> tuple[int, int, int]:
    """Best-effort parse of pass/fail/skip counts from a runner's output.

    Tries (in order):
      1. JSON envelope: ``{"passed": N, "failed": M, "skipped": K}`` printed
         anywhere in stdout (our hidden runner.sh emits this).
      2. Vitest "Tests: N passed, M failed, K skipped".
      3. Mocha "N passing".
      4. Pytest "N passed".
      5. Fallback (0/0/0).
    """
    combined = (stdout or "") + "\n" + (stderr or "")

    # 1) JSON envelope (our runner.sh ends with one).
    for match in _re.finditer(r"\{[^{}]*\"(?:passed|failed|skipped)\"[^{}]*\}", combined):
        try:
            data = _json.loads(match.group(0))
        except Exception:  # noqa: S112 — scanning for the first parseable test-count blob; bad matches are expected noise
            continue
        if isinstance(data, dict) and ("passed" in data or "failed" in data or "skipped" in data):
            return (
                int(data.get("passed", 0) or 0),
                int(data.get("failed", 0) or 0),
                int(data.get("skipped", 0) or 0),
            )

    # 2) Vitest / Jest "Tests:  3 passed, 1 failed, 0 skipped".
    m = _re.search(
        r"Tests:\s*(?:(\d+)\s+passed)?[,\s]*(?:(\d+)\s+failed)?[,\s]*(?:(\d+)\s+skipped)?",
        combined,
    )
    if m and any(m.group(i) for i in (1, 2, 3)):
        return (
            int(m.group(1) or 0),
            int(m.group(2) or 0),
            int(m.group(3) or 0),
        )

    # 3) Mocha "5 passing", "2 failing", "1 pending".
    mp = _re.search(r"(\d+)\s+passing", combined)
    mf = _re.search(r"(\d+)\s+failing", combined)
    ms = _re.search(r"(\d+)\s+pending", combined)
    if mp or mf:
        return (
            int(mp.group(1)) if mp else 0,
            int(mf.group(1)) if mf else 0,
            int(ms.group(1)) if ms else 0,
        )

    # 4) Pytest "3 passed, 1 failed, 2 skipped".
    pp = _re.search(r"(\d+)\s+passed", combined)
    pf = _re.search(r"(\d+)\s+failed", combined)
    ps = _re.search(r"(\d+)\s+skipped", combined)
    if pp or pf or ps:
        return (
            int(pp.group(1)) if pp else 0,
            int(pf.group(1)) if pf else 0,
            int(ps.group(1)) if ps else 0,
        )

    return (0, 0, 0)


# Backwards-compatible short alias — some callers / docs reference ``LocalDriver``.
LocalDriver = LocalSandboxDriver

__all__ = ["LocalDriver", "LocalSandboxDriver"]
