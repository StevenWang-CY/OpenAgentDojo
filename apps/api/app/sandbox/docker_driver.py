"""Docker-based sandbox driver.

Each session gets its own ephemeral container, dropped to no capabilities and
no network by default. Compatible with rootless Docker.

Heavy work is done off the event loop via ``asyncio.to_thread`` so blocking
SDK calls do not stall the API.
"""

from __future__ import annotations

import asyncio
import io
import re
import shlex
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from loguru import logger

from app.config import get_settings
from app.sandbox.driver import (
    InvalidRegexError,
    SandboxDriver,
    SearchMatchDict,
    SearchTimeoutError,
)
from app.sandbox.lsp import (
    DockerLSPProcess,
    LSPUnavailableError,
    spawn_docker_lsp,
)
from app.sandbox.types import (
    ApplyResult,
    FileTreeNode,
    GradingArtifacts,
    RunResult,
    SandboxHandle,
)

# Mirrors LocalSandboxDriver — see local_driver.py for the rationale.
_SEARCH_TIMEOUT_S = 10
_SEARCH_LINE_TEXT_CAP = 500
_SEARCH_GLOB_EXCLUDES: tuple[str, ...] = ("!.git/**", "!node_modules/**")

# `docker` is imported lazily — keep this module importable even when Docker
# isn't running on the host. The SDK is still listed as a hard dependency in
# pyproject.toml because the prod image always has it.


# Path to the seccomp profile shipped at infra/docker/seccomp.json.
#
# Resolution order:
#   1. ``ARENA_SECCOMP_PROFILE`` env var (escape hatch for prod images that
#      ship the profile at a non-standard path).
#   2. ``<repo-root>/infra/docker/seccomp.json`` — works in dev and tests.
#      From this file the repo root is parents[4] (sandbox→app→api→apps→repo).
#   3. ``/app/infra/docker/seccomp.json`` — the path used inside both the API
#      and sandbox-worker container images.
def _resolve_seccomp_path() -> Path:
    import os

    env = os.environ.get("ARENA_SECCOMP_PROFILE")
    if env:
        return Path(env)

    here = Path(__file__).resolve()
    repo_root = here.parents[4] if len(here.parents) >= 5 else here.parents[-1]
    candidate = repo_root / "infra" / "docker" / "seccomp.json"
    if candidate.exists():
        return candidate

    return Path("/app/infra/docker/seccomp.json")


_SECCOMP_PATH = _resolve_seccomp_path()

_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./\-]+$")


def _safe_workspace_path(path: str, workdir: str = "/workspace") -> str:
    """Resolve ``path`` against ``workdir`` and reject traversal/absolute escapes.

    Mirrors :func:`LocalSandboxDriver._resolve` so the docker driver enforces
    the same workspace-only invariant. Returns the canonical absolute path
    inside the workdir. Raises ``ValueError`` on any escape attempt.
    """
    if not path or path.isspace():
        raise ValueError("path must not be empty")
    if "\x00" in path:
        raise ValueError("path must not contain NUL bytes")
    if not _SAFE_PATH_RE.match(path.lstrip("/")):
        raise ValueError("path contains unsupported characters")

    workdir_p = PurePosixPath(workdir)
    pure = PurePosixPath(path)
    if pure.is_absolute():
        resolved = pure
    else:
        resolved = workdir_p / pure
    # Reject any '..' segment after joining.
    parts = resolved.parts
    if any(p == ".." for p in parts):
        raise ValueError("path must not contain '..' segments")
    if not str(resolved).startswith(str(workdir_p)):
        raise ValueError(f"path '{path}' escapes workspace '{workdir}'")
    return str(resolved)


class DockerSandboxDriver(SandboxDriver):
    """Driver backed by the Docker Python SDK."""

    name = "docker"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: Any | None = None  # lazy: docker.DockerClient

    # ------------------------------------------------------------------ client
    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import docker
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("docker SDK not installed") from exc
        try:
            self._client = docker.from_env()
            # Ping eagerly so failure surfaces here rather than mid-request.
            self._client.ping()
        except Exception as exc:
            raise RuntimeError(f"docker daemon unavailable: {exc}") from exc
        return self._client

    @staticmethod
    def _image_for(runtime: str) -> str:
        # The mapping must cover every value in
        # ``app.missions.manifest.LanguageRuntime``. A silent fallback to
        # node20 used to mask typos (a Python mission would have booted as
        # Node) — we raise on unknown runtimes so a manifest drift surfaces
        # at provision time instead of mid-grade.
        mapping = {
            "node20": "agentarena/node20:1",
            "python312": "agentarena/python312:1",
            "go122": "agentarena/go122:1",
        }
        try:
            return mapping[runtime]
        except KeyError as exc:
            raise ValueError(
                f"unknown language_runtime: {runtime!r}; expected one of "
                f"{sorted(mapping)!r}"
            ) from exc

    # ------------------------------------------------------------- provision
    async def provision(self, mission: Any, session_id: Any) -> SandboxHandle:
        sid = uuid.UUID(str(session_id))
        runtime = self._mission_runtime(mission)
        image = self._image_for(runtime)
        mission_id = getattr(mission, "id", None) or "unknown"

        seccomp_path = _SECCOMP_PATH
        security_opts = ["no-new-privileges:true"]
        if seccomp_path.exists():
            security_opts.append(f"seccomp={seccomp_path}")
        else:
            # Fail loud rather than silently downgrade to the Docker default.
            logger.error(
                "seccomp profile missing at {} — refusing to provision sandbox",
                seccomp_path,
            )
            raise RuntimeError(f"seccomp profile missing: {seccomp_path}")

        def _create():
            client = self._ensure_client()
            container = client.containers.create(
                image=image,
                command=["/bin/sleep", str(self._settings.sandbox_timeout_seconds)],
                detach=True,
                tty=False,
                stdin_open=False,
                network_disabled=True,
                cap_drop=["ALL"],
                mem_limit="2g",
                nano_cpus=1_000_000_000,
                # Root FS is read-only; writable scratch lives in sized tmpfs.
                read_only=True,
                tmpfs={
                    "/tmp": "size=128m,uid=1000,gid=1000",
                    "/workspace": "size=1g,uid=1000,gid=1000",
                },
                security_opt=security_opts,
                pids_limit=256,
                user="1000:1000",
                working_dir="/workspace",
                labels={
                    "arena.session_id": str(sid),
                    "arena.mission_id": str(mission_id),
                },
            )
            container.start()
            return container

        container = await asyncio.to_thread(_create)
        logger.info("docker sandbox provisioned: container={} session={}", container.id[:12], sid)

        return SandboxHandle(
            id=str(uuid.uuid4()),
            driver=self.name,
            workdir=Path("/workspace"),
            mission_id=str(mission_id),
            session_id=sid,
            container_id=container.id,
            driver_state={"image": image, "runtime": runtime},
        )

    @staticmethod
    def _mission_runtime(mission: Any) -> str:
        repo = getattr(mission, "repo", None)
        if repo is not None:
            return getattr(repo, "language_runtime", "node20")
        return "node20"

    # ------------------------------------------------------------- container
    def _container(self, handle: SandboxHandle) -> Any:
        if not handle.container_id:
            raise RuntimeError("sandbox has no container")
        client = self._ensure_client()
        return client.containers.get(handle.container_id)

    async def attach_shell(self, handle: SandboxHandle) -> Any:
        """Open a TTY-attached exec instance and return its socket."""

        def _exec():
            client = self._ensure_client()
            container = client.containers.get(handle.container_id)
            exec_id = client.api.exec_create(
                container.id,
                cmd=["/bin/bash", "-i"],
                tty=True,
                stdin=True,
                stdout=True,
                stderr=True,
                workdir=str(handle.workdir),
            )["Id"]
            sock = client.api.exec_start(exec_id, tty=True, socket=True, demux=False)
            return exec_id, sock

        return await asyncio.to_thread(_exec)

    # ----------------------------------------------------------------- files
    async def read_file(self, handle: SandboxHandle, path: str) -> bytes:
        safe = _safe_workspace_path(path, str(handle.workdir))

        def _read():
            container = self._container(handle)
            stream, _stat = container.get_archive(safe)
            buf = io.BytesIO(b"".join(stream))
            buf.seek(0)
            saw_member = False
            with tarfile.open(fileobj=buf, mode="r|") as tar:
                for member in tar:
                    saw_member = True
                    if member.isdir():
                        # Explicitly signal directory so the caller can 404
                        # rather than silently returning empty bytes (P1-B22).
                        raise IsADirectoryError(safe)
                    if member.isfile():
                        extracted = tar.extractfile(member)
                        if extracted is None:
                            return b""
                        return extracted.read()
            if not saw_member:
                raise FileNotFoundError(safe)
            return b""

        return await asyncio.to_thread(_read)

    async def write_file(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        safe = _safe_workspace_path(path, str(handle.workdir))
        target_path = PurePosixPath(safe)

        def _write():
            container = self._container(handle)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                info = tarfile.TarInfo(name=target_path.name)
                info.size = len(content)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(content))
            buf.seek(0)
            container.put_archive(str(target_path.parent) or str(handle.workdir), buf.read())

        await asyncio.to_thread(_write)

    async def list_tree(self, handle: SandboxHandle, root: str = "/workspace") -> FileTreeNode:
        # We shell out to `find`: cheaper than fetching the whole archive.
        # Null-separated output so paths containing whitespace (incl. tabs)
        # parse correctly (P1-B22).
        result = await self.run(
            handle,
            [
                "/bin/sh",
                "-c",
                f"cd {shlex.quote(root)} && find . -maxdepth 8 -mindepth 1 "
                "-not -path '*/\\.git*' -printf '%y\\0%s\\0%p\\0'",
            ],
            timeout_s=15,
        )
        out = FileTreeNode(path=root, kind="dir")
        tokens = result.stdout.split("\0")
        # Records come in triples (kind, size, path); ignore the trailing empty.
        for i in range(0, len(tokens) - 1, 3):
            triple = tokens[i : i + 3]
            if len(triple) < 3:
                continue
            kind_char, size_str, rel = triple
            try:
                size = int(size_str)
            except ValueError:
                size = 0
            out.children.append(
                FileTreeNode(
                    path=rel.removeprefix("./") or ".",
                    kind="dir" if kind_char == "d" else "file",
                    size=size,
                )
            )
        return out

    async def diff_from_initial(self, handle: SandboxHandle) -> str:
        result = await self.run(handle, ["git", "--no-pager", "diff", "HEAD"], timeout_s=30)
        return result.stdout

    # ----------------------------------------------------------- find / search
    async def list_files(
        self,
        handle: SandboxHandle,
        *,
        max_files: int = 5000,
    ) -> list[str]:
        """Return repo-relative paths from ``git ls-files`` inside the container.

        Mirrors :meth:`LocalSandboxDriver.list_files`; the docker driver just
        wraps the same ``git ls-files`` invocation through ``exec``.
        """
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
            cwd=str(handle.workdir),
        )
        if result.exit_code != 0:
            logger.warning(
                "[list_files] git ls-files failed in container (exit={}): stderr={!r}",
                result.exit_code,
                (result.stderr or "")[:300],
            )
            return []

        raw_paths = [p for p in result.stdout.split("\x00") if p]
        capped = raw_paths[:max_files]
        capped.sort(key=lambda p: (p.count("/"), p))
        return capped

    async def search(  # noqa: PLR0912 — branch count dominated by input-flag handling
        self,
        handle: SandboxHandle,
        query: str,
        *,
        glob: str | None,
        case_sensitive: bool,
        regex: bool,
        max_results: int,
    ) -> tuple[list[SearchMatchDict], bool, int, int]:
        """Execute ``rg --json`` inside the container and parse results.

        The container image ships ripgrep so we just shell out via the
        existing ``run`` plumbing — it already handles timeout-kill of a
        single exec without touching siblings (terminal PTY, grading suites).
        """
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
        argv.extend(["--", query])

        result = await self.run(
            handle,
            cmd=argv,
            timeout_s=_SEARCH_TIMEOUT_S,
            cwd=str(handle.workdir),
        )

        if result.timed_out:
            raise SearchTimeoutError(
                f"search exceeded {_SEARCH_TIMEOUT_S}s budget",
            )

        if result.exit_code == 2:
            if regex and (
                "regex parse error" in result.stderr
                or "PCRE2" in result.stderr
                or "error parsing regex" in result.stderr
            ):
                raise InvalidRegexError(result.stderr.strip()[:300])
            logger.warning(
                "[search] ripgrep returned 2 in container: stderr={!r}",
                result.stderr[:300],
            )
            # Phase 4.A.19 — return the real exit code so the router can
            # surface it via ``command.run`` + ``validator.flag``.
            return [], False, 0, int(result.exit_code)

        matches: list[SearchMatchDict] = []
        truncated = False
        for line in result.stdout.splitlines():
            if not line:
                continue
            if len(matches) >= max_results:
                truncated = True
                break
            parsed = _parse_rg_json_match_str(line)
            if parsed is None:
                continue
            matches.append(parsed)
        return matches, truncated, len(matches), int(result.exit_code)

    # ------------------------------------------------------------------- run
    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_s: int = 60,
        cwd: str | None = None,
    ) -> RunResult:
        exec_id_holder: dict[str, str] = {}

        def _exec_run():
            container = self._container(handle)
            client = self._ensure_client()
            kwargs: dict[str, Any] = {"tty": False}
            if cwd:
                kwargs["workdir"] = cwd
            # Two-step create+start so we can capture the exec id and signal
            # it on timeout (P1-B21). ``exec_run`` hides the id.
            exec_id = client.api.exec_create(container.id, cmd=cmd, **kwargs)["Id"]
            exec_id_holder["id"] = exec_id
            stream = client.api.exec_start(exec_id, demux=True, tty=False)
            stdout_b, stderr_b = stream if isinstance(stream, tuple) else (stream, b"")
            info = client.api.exec_inspect(exec_id)
            exit_code = info.get("ExitCode")
            return exit_code, (stdout_b or b"", stderr_b or b"")

        def _kill_exec() -> None:
            """Best-effort kill of THIS exec process — not the container.

            Previously this called ``container.kill(SIGKILL)`` which nuked
            every concurrent exec (PTY shell, parallel grading phase). We
            now send the signal to the specific exec's PID via the host's
            kernel signalling, falling back to a no-op if the PID has
            already exited.
            """
            exec_id = exec_id_holder.get("id")
            if not exec_id:
                return
            try:
                client = self._ensure_client()
                info = client.api.exec_inspect(exec_id)
                pid = info.get("Pid")
                if not pid:
                    return
                # Signal the exec inside the container — use ``kill -TERM``
                # via a tiny shell exec so we don't depend on host PID
                # namespace mapping. If that fails we fall back to a hard
                # SIGKILL on the same PID.
                try:
                    container = self._container(handle)
                    container.exec_run(
                        ["/bin/sh", "-c", f"kill -TERM {int(pid)} 2>/dev/null || true"],
                        detach=True,
                    )
                except Exception:  # pragma: no cover — best-effort
                    pass
            except Exception as exc:  # pragma: no cover — best-effort
                logger.debug("docker exec kill failed for {}: {}", exec_id, exc)

        started = time.monotonic()
        timed_out = False
        try:
            exit_code, (stdout_b, stderr_b) = await asyncio.wait_for(
                asyncio.to_thread(_exec_run), timeout=timeout_s
            )
        except TimeoutError:
            timed_out = True
            await asyncio.to_thread(_kill_exec)
            exit_code, stdout_b, stderr_b = -1, b"", b"timeout"

        return RunResult(
            exit_code=int(exit_code) if exit_code is not None else -1,
            stdout=(stdout_b or b"").decode("utf-8", errors="replace"),
            stderr=(stderr_b or b"").decode("utf-8", errors="replace"),
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
            command=" ".join(cmd),
        )

    # ------------------------------------------------------------------ diff
    async def apply_diff(self, handle: SandboxHandle, diff_text: str) -> ApplyResult:
        # Random suffix prevents two concurrent apply_diff calls on the
        # same sandbox from trampling each other's diff bytes (the previous
        # ``/tmp/p.diff`` shared name silently corrupted concurrent agent
        # turns + grader runs). Write into /tmp (tmpfs), apply, then remove.
        diff_name = f"/tmp/arena-patch-{uuid.uuid4().hex}.diff"

        # Bypass the workspace-path guard for the tmp scratch file by
        # constructing the tarball directly — apply_diff needs to write
        # outside /workspace.
        def _write_tmp_diff() -> None:
            container = self._container(handle)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                payload = diff_text.encode("utf-8")
                info = tarfile.TarInfo(name=Path(diff_name).name)
                info.size = len(payload)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(payload))
            buf.seek(0)
            container.put_archive("/tmp", buf.read())

        await asyncio.to_thread(_write_tmp_diff)
        try:
            result = await self.run(
                handle,
                ["git", "apply", "--3way", "--whitespace=fix", diff_name],
                timeout_s=30,
            )
        finally:
            # Best-effort cleanup; loop won't stall if the rm fails.
            try:
                await self.run(handle, ["rm", "-f", diff_name], timeout_s=5)
            except Exception:  # pragma: no cover — best-effort
                pass

        if result.exit_code != 0:
            return ApplyResult(applied=False, error=result.stderr or result.stdout)

        names = await self.run(handle, ["git", "diff", "--name-only", "HEAD"], timeout_s=10)
        stat = await self.run(handle, ["git", "diff", "--numstat", "HEAD"], timeout_s=10)
        files = [f for f in names.stdout.splitlines() if f]
        added = removed = 0
        for line in stat.stdout.splitlines():
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

    # ------------------------------------------------------------ freeze
    async def freeze_and_grade(
        self,
        handle: SandboxHandle,
        mission: Any,
        *,
        manifest_folder: Path | None = None,
    ) -> GradingArtifacts:
        """Real M5 grading: snapshot diff, copy hidden tests, run visible + hidden suites."""
        diff = await self.diff_from_initial(handle)

        # Mount hidden tests under /grader/hidden_tests via put_archive.
        hidden_dir = manifest_folder / "hidden_tests" if manifest_folder is not None else None
        if hidden_dir is not None and hidden_dir.is_dir():
            await self._put_directory(handle, hidden_dir, "/grader/hidden_tests")

        # Freeze workspace (best-effort).
        await self.run(handle, ["chmod", "-R", "a-w", "/workspace"], timeout_s=30)

        test_results: dict[str, Any] = {}
        logs: dict[str, str] = {}

        # Visible suites.
        repo = getattr(mission, "repo", None)
        cmds: dict[str, str] = dict(getattr(repo, "test_commands", {}) if repo is not None else {})
        for suite, cmd in cmds.items():
            tr = await self._run_test_phase(handle, suite, cmd, 180)
            test_results[suite] = _docker_test_run_to_dict(tr)
            logs[f"visible.{suite}"] = tr.stdout + "\n--- stderr ---\n" + tr.stderr

        # Hidden suite. The runner is expected under /grader/hidden_tests/
        # (mounted outside /workspace so the user can't list or pre-pass it).
        # Mission YAMLs that still write ``bash hidden_tests/runner.sh`` are
        # rewritten to the canonical /grader path; everything else passes
        # through verbatim so missions can specify custom commands.
        if hidden_dir is not None and hidden_dir.is_dir():
            hidden_cfg = getattr(mission, "hidden_tests", None)
            default_cmd = "bash /grader/hidden_tests/runner.sh"
            raw_cmd = (
                getattr(hidden_cfg, "command", default_cmd)
                if hidden_cfg is not None
                else default_cmd
            ) or default_cmd
            # Word-boundary-safe rewrite: only legacy ``hidden_tests/``
            # references (no ``/grader/`` prefix already) get rewritten to
            # the canonical mount path.
            hidden_cmd = re.sub(r"(?<![\w/])hidden_tests/", "/grader/hidden_tests/", raw_cmd)
            wrapped = f"WORKSPACE_DIR=/workspace GRADER_DIR=/grader/hidden_tests {hidden_cmd}"
            tr = await self._run_test_phase(handle, "hidden", wrapped, 180)
            test_results["hidden"] = _docker_test_run_to_dict(tr)
            logs["hidden"] = tr.stdout + "\n--- stderr ---\n" + tr.stderr
        elif manifest_folder is not None:
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

    async def _put_directory(
        self,
        handle: SandboxHandle,
        local_dir: Path,
        target_dir: str,
    ) -> None:
        """Tar up ``local_dir`` and put_archive into ``target_dir`` inside the container."""

        def _make_tar() -> bytes:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                for path in local_dir.rglob("*"):
                    if not path.is_file():
                        continue
                    info = tarfile.TarInfo(name=str(path.relative_to(local_dir)))
                    data = path.read_bytes()
                    info.size = len(data)
                    info.mode = 0o644
                    tar.addfile(info, io.BytesIO(data))
            buf.seek(0)
            return buf.read()

        archive = await asyncio.to_thread(_make_tar)
        await self.run(handle, ["mkdir", "-p", target_dir], timeout_s=15)

        def _put() -> None:
            container = self._container(handle)
            container.put_archive(target_dir, archive)

        await asyncio.to_thread(_put)

    async def _run_test_phase(
        self,
        handle: SandboxHandle,
        suite: str,
        cmd: str,
        timeout_s: int,
    ) -> _DockerTestPhaseResult:
        workdir = getattr(handle, "workdir", None)
        cwd = str(workdir) if workdir is not None else "/workspace"
        result = await self.run(
            handle,
            ["bash", "-lc", cmd],
            timeout_s=timeout_s,
            cwd=cwd,
        )
        if result.timed_out:
            return _DockerTestPhaseResult(
                suite=suite,
                exit_code=max(1, result.exit_code),
                stdout=result.stdout,
                stderr=result.stderr + f"\n[test phase '{suite}' timed out after {timeout_s}s]",
                passed=0,
                failed=0,
                skipped=0,
                timed_out=True,
            )
        passed, failed, skipped = _docker_parse_counts(result.stdout, result.stderr)
        return _DockerTestPhaseResult(
            suite=suite,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            passed=passed,
            failed=failed,
            skipped=skipped,
            timed_out=False,
        )

    # ------------------------------------------------------------------ ping
    async def ping(self) -> bool:
        """Verify the Docker daemon is reachable.

        Called from ``/healthz/ready`` so a sandbox daemon outage shows up as
        a 503 instead of letting traffic hit the API and fail at provision
        time. Runs in a thread because the underlying SDK call is sync.
        """

        def _do_ping() -> bool:
            client = self._ensure_client()
            return bool(client.ping())

        return await asyncio.to_thread(_do_ping)

    # ------------------------------------------------------------------ lsp
    async def spawn_lsp(self, handle: SandboxHandle, language: str) -> DockerLSPProcess:
        """Launch the LSP inside the user's container via ``docker exec``.

        Wraps the SDK plumbing in :func:`app.sandbox.lsp.spawn_docker_lsp`.
        The bidirectional socket inherits the container's seccomp profile,
        memory cap, and ``--network=none`` posture, so no new isolation
        boundary is needed.

        Lifts SDK/daemon-unreachable errors into
        :class:`app.sandbox.lsp.LSPUnavailableError` with
        ``driver_unavailable`` so the WS proxy can fail soft (FE sees a
        structured ``lsp_error`` frame) instead of returning a 500.
        """
        try:
            client = self._ensure_client()
            container = client.containers.get(handle.container_id)
        except RuntimeError as exc:
            # Docker daemon down / SDK missing — log loudly but don't crash
            # the WS. The proxy converts this into ``driver_unavailable``
            # frame so the FE shows a "LSP unavailable" chip and keeps the
            # editor usable for plain syntax highlighting.
            logger.warning("lsp[{}]: docker driver unavailable: {}", language, exc)
            raise LSPUnavailableError("driver_unavailable", language, detail=str(exc)) from exc

        return await spawn_docker_lsp(
            container,
            client,
            language,
            workdir=str(handle.workdir),
        )

    # -------------------------------------------------------------- destroy
    async def destroy(self, handle: SandboxHandle) -> None:
        if not handle.container_id:
            return

        def _destroy():
            try:
                client = self._ensure_client()
                container = client.containers.get(handle.container_id)
                container.remove(force=True, v=True)
            except Exception as exc:
                logger.warning("docker sandbox destroy failed: {}", exc)

        await asyncio.to_thread(_destroy)


# ---------------------------------------------------------------------------
# Test-phase parsing helpers (used by freeze_and_grade)
# ---------------------------------------------------------------------------


import json as _json  # noqa: E402
import re as _re  # noqa: E402
from dataclasses import dataclass as _dataclass  # noqa: E402


@_dataclass(slots=True)
class _DockerTestPhaseResult:
    suite: str
    exit_code: int
    stdout: str
    stderr: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    timed_out: bool = False


def _docker_test_run_to_dict(result: _DockerTestPhaseResult) -> dict[str, Any]:
    return {
        "suite": result.suite,
        "exit_code": result.exit_code,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-4000:],
        "passed": result.passed,
        "failed": result.failed,
        "skipped": result.skipped,
        "timed_out": result.timed_out,
    }


def _docker_parse_counts(stdout: str, stderr: str) -> tuple[int, int, int]:
    """JSON-first, then test-runner-style fallback."""
    combined = (stdout or "") + "\n" + (stderr or "")
    for match in _re.finditer(r"\{[^{}]*\"(?:passed|failed|skipped)\"[^{}]*\}", combined):
        try:
            data = _json.loads(match.group(0))
        except Exception:  # noqa: S112 — scanning for the first parseable test-count blob; bad matches are expected noise
            continue
        if isinstance(data, dict):
            return (
                int(data.get("passed", 0) or 0),
                int(data.get("failed", 0) or 0),
                int(data.get("skipped", 0) or 0),
            )

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

    mp = _re.search(r"(\d+)\s+passing", combined)
    mf = _re.search(r"(\d+)\s+failing", combined)
    ms = _re.search(r"(\d+)\s+pending", combined)
    if mp or mf:
        return (
            int(mp.group(1)) if mp else 0,
            int(mf.group(1)) if mf else 0,
            int(ms.group(1)) if ms else 0,
        )

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


def _parse_rg_json_match_str(line: str) -> SearchMatchDict | None:  # noqa: PLR0912 — defensive parser
    """Parse one ``rg --json`` line (string form) into a :data:`SearchMatchDict`.

    Identical contract to ``local_driver._parse_rg_json_match`` but takes a
    decoded ``str`` because :meth:`DockerSandboxDriver.run` already decodes
    its stdout. We re-implement rather than import to keep the docker driver
    a leaf in the module graph (no inter-driver dependencies).
    """
    try:
        obj = _json.loads(line)
    except _json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "match":
        return None
    data = obj.get("data")
    if not isinstance(data, dict):
        return None

    path_obj = data.get("path")
    if not isinstance(path_obj, dict):
        return None
    path_text = path_obj.get("text")
    if not isinstance(path_text, str) or not path_text:
        return None
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
    if line_text.endswith("\n"):
        line_text = line_text[:-1]
    if len(line_text) > _SEARCH_LINE_TEXT_CAP:
        return None

    submatches = data.get("submatches")
    if not isinstance(submatches, list) or not submatches:
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
        match_start = max(0, min(match_start, len(line_text)))
        match_end = max(match_start, min(match_end, len(line_text)))

    return SearchMatchDict(
        path=path_text,
        line_number=line_number,
        line_text=line_text,
        match_start=match_start,
        match_end=match_end,
    )


# Backwards-compatible short alias — some callers / docs reference ``DockerDriver``.
DockerDriver = DockerSandboxDriver

__all__ = ["DockerDriver", "DockerSandboxDriver"]
