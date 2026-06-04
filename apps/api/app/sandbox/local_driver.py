"""Local subprocess + git-based sandbox driver.

Used when ``SANDBOX_DRIVER=local``. Every session gets a ``tempfile.mkdtemp``
under ``SANDBOX_WORKDIR``. The repo pack (if present) is copied in and a
``git init`` + initial commit is created so ``diff_from_initial`` works.

This driver is for **development only** — it provides no isolation. The app
logs a loud warning when this driver is active.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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
from app.sandbox.driver import (
    InvalidRegexError,
    SandboxDriver,
    SearchMatchDict,
    SearchTimeoutError,
)
from app.sandbox.lsp import LocalLSPProcess, spawn_local_lsp
from app.sandbox.repo_pack import RepoPackNotFoundError, load_repo_pack
from app.sandbox.types import (
    ApplyResult,
    FileTreeNode,
    GradingArtifacts,
    RunResult,
    SandboxHandle,
)

# Wall-clock budget for a single search invocation. The 10s figure matches the
# spec's worst-case "ripgrep across a fresh repo pack" measurement; anything
# longer is almost always a regex pathology (catastrophic backtracking) rather
# than legitimate work, so a hard kill + 504 to the FE is the right behaviour.
_SEARCH_TIMEOUT_S = 10.0
# Hard line-text cap (mirrors the schema's MAX_SEARCH_LINE_TEXT) — applied at
# the driver layer so even a corrupt --max-columns flag can't leak more than
# this into the API response. Lines longer than the cap are dropped.
_SEARCH_LINE_TEXT_CAP = 500
# Directories ripgrep should skip regardless of .gitignore content. Hidden
# files (--hidden) are scanned everywhere else; these two paths are dropped
# unconditionally because:
#   * .git/ — VCS metadata, near-guaranteed to dominate any honest hit list.
#   * node_modules/ — covered by .gitignore in most repos but the local
#     fallback driver hosts repo packs with vendored node_modules trees
#     symlinked from the pnpm store, and grepping that surface trivially
#     times out the search.
_SEARCH_GLOB_EXCLUDES: tuple[str, ...] = ("!.git/**", "!node_modules/**")


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
        workdir = Path(tempfile.mkdtemp(prefix=f"arena-{sid}-", dir=str(self.root))).resolve()  # noqa: ASYNC240

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

    # ----------------------------------------------------------- find / search
    async def list_files(
        self,
        handle: SandboxHandle,
        *,
        max_files: int = 5000,
    ) -> list[str]:
        """Return repo-relative paths from ``git ls-files`` (gitignore-aware).

        We deliberately combine ``--cached`` (tracked) + ``--others`` (untracked
        but not ignored) so a freshly-edited file the user just created shows
        up in the quick-open palette without needing a manual ``git add`` first.
        """
        # ``--exclude-standard`` honours ``.gitignore`` / ``.git/info/exclude``
        # / global excludes the same way every other git porcelain does.
        result = await self.run(
            handle,
            cmd=[
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            timeout_s=15,
        )
        if result.exit_code != 0:
            logger.warning(
                "[list_files] git ls-files failed (exit={}): stderr={!r}",
                result.exit_code,
                (result.stderr or "")[:300],
            )
            return []

        # `-z` produces NUL-separated entries — safe for paths containing
        # whitespace. The output may include a trailing empty after the final
        # NUL; filter it out.
        raw_paths = [p for p in result.stdout.split("\x00") if p]
        # Cap before sort so a pathological listing can't blow up memory/CPU.
        capped = raw_paths[:max_files]
        capped.sort(key=_path_sort_key)
        return capped

    async def search(  # noqa: PLR0912 — branch count is dominated by input-flag handling
        self,
        handle: SandboxHandle,
        query: str,
        *,
        glob: str | None,
        case_sensitive: bool,
        regex: bool,
        max_results: int,
    ) -> tuple[list[SearchMatchDict], bool, int, int]:
        """Run ripgrep against the workspace and parse the JSON stream.

        Implementation notes:
          * ``--no-config`` makes the result stable regardless of any
            user-level ``ripgreprc`` that might toggle smart case or alter the
            globs we depend on.
          * ``--max-count`` is set to ``max_results`` so each file contributes
            no more than the cap; the per-driver ``max_results`` slice below
            then enforces the global cap across files.
          * ``--max-columns 500`` keeps individual line payloads bounded; the
            driver layer additionally drops lines whose post-truncation text
            still exceeds :data:`_SEARCH_LINE_TEXT_CAP` (defence-in-depth
            against a malformed ripgrep build).
          * The walk is gitignore-aware by default; we add a no-VCS exclude
            for ``.git/`` and a ``!node_modules/**`` glob defensively (see
            module-level constant).
        """
        # Build the ripgrep argv. We use ``--json`` to get a structured stream
        # (one JSON object per line) so we don't have to parse the textual
        # output, which would be fragile under different ripgrep versions.
        argv: list[str] = [
            "rg",
            "--no-config",
            "--json",
            "--line-number",
            "--with-filename",
            "--hidden",
            "--max-columns",
            "500",
            "--max-count",
            str(max_results),
        ]
        for excl in _SEARCH_GLOB_EXCLUDES:
            argv.extend(["--glob", excl])
        if glob:
            argv.extend(["--glob", glob])
        if case_sensitive:
            argv.append("--case-sensitive")
        else:
            argv.append("--ignore-case")
        if regex:
            argv.append("--pcre2")
        else:
            argv.append("--fixed-strings")
        # ``--`` ends option parsing so a pattern starting with ``-`` is
        # treated as a search term, not a flag.
        argv.extend(["--", query])

        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(handle.workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=_SEARCH_TIMEOUT_S
            )
        except TimeoutError as exc:
            # Drain the subprocess so we don't leak a child PID. ``kill``
            # is idempotent; ``communicate`` then collects any buffered
            # bytes for the post-mortem log.
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.communicate()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "[search] ripgrep timed out after {}ms (query={!r})",
                elapsed_ms,
                query[:60],
            )
            raise SearchTimeoutError(
                f"search exceeded {_SEARCH_TIMEOUT_S}s budget",
            ) from exc

        # ripgrep exit codes:
        #   0 — at least one match.
        #   1 — no matches (also fired when a regex compiles but matches nothing).
        #   2 — error (bad pattern, IO, etc.).
        exit_code = proc.returncode if proc.returncode is not None else -1
        if exit_code == 2:
            stderr_text = (stderr_b or b"").decode("utf-8", errors="replace")
            # Distinguish a bad regex (the only error worth surfacing as a
            # 400) from other ripgrep faults (IO, glob, etc.). The error
            # banner ripgrep prints starts with the pattern itself plus a
            # ``regex parse error``/``PCRE2`` marker on >=14.x.
            if regex and (
                "regex parse error" in stderr_text
                or "PCRE2" in stderr_text
                or "error parsing regex" in stderr_text
            ):
                raise InvalidRegexError(stderr_text.strip()[:300])
            logger.warning(
                "[search] ripgrep returned 2: stderr={!r}",
                stderr_text[:300],
            )
            # Phase 4.A.19 — pass the real exit code through to the router
            # so it can emit ``exit_code=2`` on the ``command.run`` event
            # AND fire a ``validator.flag{kind="search_error"}``. The
            # historical behaviour silently collapsed this to "no
            # matches" + exit_code=0, which made a chronically broken
            # workspace search invisible on dashboards.
            return [], False, 0, exit_code

        matches: list[SearchMatchDict] = []
        truncated = False
        for line in (stdout_b or b"").splitlines():
            if not line:
                continue
            if len(matches) >= max_results:
                truncated = True
                break
            parsed = _parse_rg_json_match(line)
            if parsed is None:
                continue
            matches.append(parsed)
        # If ripgrep capped via --max-count we may have hit the global cap
        # exactly — flag truncated only when we actually stopped reading.
        return matches, truncated, len(matches), exit_code

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
        # has no business being world-readable on the host. UUID suffix
        # matches the docker driver — without it, two in-flight applies
        # on the same handle race on the same filename.
        diff_file = handle.workdir / f".arena_patch_{uuid.uuid4().hex}.diff"
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
        # Stage the grader OUTSIDE the workdir (a sibling dir), matching the
        # production ``/grader`` mount. Keeping it inside ``handle.workdir``
        # both leaks the hidden tests to the user's /tree+/file endpoints AND
        # — for module-scoped toolchains like Go — pollutes the visible
        # suite: ``go test ./...`` would descend into ``grader/hidden_tests``
        # and run the (still-red) hidden tests as part of the visible run.
        grader_root = handle.workdir.parent / f"{handle.workdir.name}.grader"
        hidden_dir = manifest_folder / "hidden_tests" if manifest_folder is not None else None
        if hidden_dir is not None and hidden_dir.is_dir():
            target = grader_root / "hidden_tests"
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
            #
            # Go (and any cross-language) runners shell out to a shared
            # runner bridge that the production image stages at
            # ``/opt/runners``. The local sandbox only contains the repo
            # pack, so that path is absent and the runner's in-tree
            # fallback (``$GRADER_DIR/../../_shared/docker/runners``)
            # resolves to a directory that was never copied in. Point
            # ``RUNNERS_DIR`` at the real in-repo runners so go-runner.sh
            # (and friends) resolve under the local driver. ``hidden_dir``
            # is ``missions/<id>/hidden_tests`` → the runners live two
            # parents up under ``_shared/docker/runners``.
            runners_dir = hidden_dir.parent.parent / "_shared" / "docker" / "runners"
            grader_dir = grader_root / "hidden_tests"
            # ``export VAR; cmd`` — NOT inline ``VAR=val cmd "$VAR"``. The
            # default hidden command is ``bash "$GRADER_DIR/runner.sh"``;
            # with an inline env-prefix the outer ``bash -lc`` expands
            # ``$GRADER_DIR`` *before* applying the assignment (POSIX
            # ordering), so it resolves to the empty string and the runner
            # is invoked as ``bash /runner.sh`` (exit 127). Exporting in a
            # preceding statement makes the variable visible to the
            # expansion in the same shell.
            exports = [
                f"export WORKSPACE_DIR={shlex.quote(str(handle.workdir))}",
                f"export GRADER_DIR={shlex.quote(str(grader_dir))}",
            ]
            if runners_dir.is_dir():
                exports.insert(0, f"export RUNNERS_DIR={shlex.quote(str(runners_dir))}")
            wrapped = "; ".join(exports) + "; " + hidden_cmd
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
        return _re.sub(r"(?<![\w/])hidden_tests/", '"$GRADER_DIR"/', raw)

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

    async def spawn_lsp(self, handle: SandboxHandle, language: str) -> LocalLSPProcess:
        """Spawn an LSP host-side subprocess with ``cwd`` pinned at the workdir.

        Delegates the PATH probe + ``asyncio.create_subprocess_exec`` to
        :func:`app.sandbox.lsp.spawn_local_lsp`. In dev/test environments
        the language-server binary is frequently absent — :class:`app.sandbox.lsp.LSPUnavailableError`
        with ``binary_not_found`` is the expected outcome, and the WS proxy
        translates it into a structured ``lsp_error`` frame.
        """
        return await spawn_local_lsp(language, cwd=str(handle.workdir))

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
        # Remove the sibling grader stage (hidden tests live OUTSIDE the
        # workdir — see freeze_and_grade).
        shutil.rmtree(
            handle.workdir.parent / f"{handle.workdir.name}.grader",
            ignore_errors=True,
        )


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


def _path_sort_key(path: str) -> tuple[int, str]:
    """Deterministic sort: shallower paths first, then alphabetical.

    The depth ordering surfaces top-level entrypoints (``package.json``,
    ``README.md``) above deeply-nested files in the quick-open palette, which
    matches the mental model of someone scanning for the file they want.
    """
    depth = path.count("/")
    return (depth, path)


def _parse_rg_json_match(line: bytes) -> SearchMatchDict | None:  # noqa: PLR0912 — defensive parser
    """Parse a single ``rg --json`` stdout line into a :data:`SearchMatchDict`.

    Ripgrep emits a stream of JSON objects, one per line. We only care about
    objects with ``type == "match"`` — the ``begin``/``end``/``summary``
    envelopes are ignored. ``data.lines.text`` may be missing on binary
    matches (ripgrep gives ``bytes`` instead); those are dropped because the
    FE can't render them anyway.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "match":
        return None
    data = obj.get("data")
    if not isinstance(data, dict):
        return None

    # Path is wrapped in ``{"text": "..."}`` or ``{"bytes": "..."}`` depending
    # on whether the path was valid UTF-8. We only handle the text case; a
    # path with non-UTF-8 bytes is almost certainly noise and dropping it is
    # better than surfacing garbled output to the user.
    path_obj = data.get("path")
    if not isinstance(path_obj, dict):
        return None
    path_text = path_obj.get("text")
    if not isinstance(path_text, str) or not path_text:
        return None
    # Strip the leading "./" ripgrep emits when walking from cwd.
    if path_text.startswith("./"):
        path_text = path_text[2:]

    line_number = data.get("line_number")
    if not isinstance(line_number, int) or line_number < 1:
        return None

    lines = data.get("lines")
    if not isinstance(lines, dict):
        return None
    line_text = lines.get("text")
    if not isinstance(line_text, str):
        return None
    # Trim the trailing newline ripgrep keeps in ``lines.text``.
    if line_text.endswith("\n"):
        line_text = line_text[:-1]
    # Defence-in-depth: drop matches whose post-truncation line still busts
    # the column cap. The router-level schema would clip it; dropping here
    # keeps the payload bounded with no surprises further down.
    if len(line_text) > _SEARCH_LINE_TEXT_CAP:
        return None

    submatches = data.get("submatches")
    if not isinstance(submatches, list) or not submatches:
        # Without a submatch, we have nothing to highlight — degrade by
        # pointing at the whole line.
        match_start = 0
        match_end = len(line_text)
    else:
        first = submatches[0]
        if not isinstance(first, dict):
            return None
        match_start = first.get("start", 0)
        match_end = first.get("end", 0)
        if not isinstance(match_start, int) or not isinstance(match_end, int):
            return None
        # Clamp to the (possibly truncated) line text.
        match_start = max(0, min(match_start, len(line_text)))
        match_end = max(match_start, min(match_end, len(line_text)))

    return SearchMatchDict(
        path=path_text,
        line_number=line_number,
        line_text=line_text,
        match_start=match_start,
        match_end=match_end,
    )


# Backwards-compatible short alias — some callers / docs reference ``LocalDriver``.
LocalDriver = LocalSandboxDriver

__all__ = ["LocalDriver", "LocalSandboxDriver"]
