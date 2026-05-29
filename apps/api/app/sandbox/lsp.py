"""Long-lived LSP stdio processes managed by the sandbox drivers (P1-3).

Each driver subclasses :class:`LSPProcess` with whatever underlying transport
makes sense for it:

* :class:`LocalLSPProcess` wraps an ``asyncio.subprocess`` running on the host
  (used by ``LocalSandboxDriver``; dev/test only).
* :class:`DockerLSPProcess` wraps a bidirectional docker ``exec`` socket
  attached to the user's running sandbox container.

The WebSocket proxy (:mod:`app.ws.lsp`) only ever calls into the abstract
surface — ``write_stdin``, ``read_stdout``, ``shutdown`` — so it is wire-
compatible with whichever driver provisioned the session.

The bytes flowing through these processes are **never** inspected by the
platform. The whole point of P1-3 is that JSON-RPC frames go from the FE's
``monaco-languageclient`` to the language server inside the sandbox and back,
with the API box acting as a pure pump. Parsing JSON-RPC here would couple
us to mid-stream LSP protocol revisions and defeat the design.
"""

from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Final, Literal

from loguru import logger

if TYPE_CHECKING:
    pass

# Closed set of error-class discriminators. Encoded as a ``Literal`` so a
# typo at a ``lsp_errors_total.labels(error_class=...)`` callsite (or an
# :class:`LSPUnavailableError` construction) is a mypy error rather than a
# silently-mislabelled Prometheus metric. New classes MUST be added here
# together with the corresponding dashboard / alert wiring.
LSPErrorClass = Literal[
    "binary_not_found",
    "spawn_failed",
    "unsupported_language",
    "driver_unavailable",
    "lsp_already_running",
    "session_not_active",
    "session_not_found",
    "no_sandbox",
    "shutdown_timeout",
    "stdin_broken_pipe",
    "terminate_failed",
    "kill_failed",
    "socket_recv_failed",
    "exec_inspect_failed",
    "dead_entry_evicted",
    "lsp_crashed",
    "initialize_timeout",
    "ws_token_failed",
    # P1-3 remediation additions:
    # * ``sandbox_busy``  — an apply-patch (or other exclusive mutation) is
    #   in flight on this sandbox; the WS proxy MUST refuse a fresh LSP
    #   attach because spawning a long-lived stdio process mid-patch can
    #   race the workspace tree the patch is rewriting underneath us.
    # * ``lsp_oom``       — the language server's process was reaped by the
    #   kernel OOM killer (docker exit code 137 / signal 9). Surfaced as a
    #   structured frame so the FE can show "Memory cap hit, falling back
    #   to syntax-only" instead of the generic "lsp_crashed" copy.
    # * ``origin_forbidden`` — the WS upgrade carried an ``Origin`` header
    #   not on the API's CORS allow-list. We refuse the upgrade BEFORE
    #   accepting the socket; emitted symmetrically with the other classes
    #   so dashboards keep one error-class taxonomy.
    "sandbox_busy",
    "lsp_oom",
    "origin_forbidden",
]


# Per-language memory budgets (MiB) for the spawned LSP. These are the design
# values from §P1-3 — the docker sandbox container has a hard 2 GiB ceiling so
# a runaway language server cannot starve the host, but the per-language cap
# narrows that further to prevent ONE wedged server from monopolising the
# whole container budget. Enforcement is best-effort:
#
#   * Docker driver: a ``prlimit --as=`` wrapper around the LSP argv is
#     applied at spawn time when ``prlimit`` is on the container image's
#     PATH. The fallback is documented intent — the 2 GiB container ceiling
#     still backs it.
#   * Local driver:  ``resource.setrlimit(RLIMIT_AS, ...)`` in the child
#     pre-exec hook. Best-effort because macOS dev hosts don't honour AS
#     limits the same way Linux does.
#
# Bumping a value here is a load-bearing change — coordinate with the SRE
# dashboards that alert on ``container_memory_working_set_bytes`` per
# language label.
LSP_MEMORY_BUDGETS_MB: Final[dict[str, int]] = {
    "python": 256,
    "typescript": 512,
    "go": 384,
}


# Exit codes / signals that indicate the kernel OOM killer reaped the LSP.
# Docker surfaces ``137`` (= 128 + SIGKILL) on ``exec_inspect`` after the
# cgroup OOM trigger fires; raw subprocess paths see ``-9`` from
# ``Process.returncode`` (Python negates the signal). Either is treated as
# the same event from the WS proxy's perspective.
LSP_OOM_EXIT_CODES: Final[frozenset[int]] = frozenset({137, -9, 9})


# Dedicated, bounded executor for ``DockerLSPProcess.read_stdout`` so a stuck
# language-server socket cannot starve the default loop executor (whose pool
# is shared with anything else that calls ``run_in_executor(None, ...)``).
# Sized to the documented per-process LSP concurrency ceiling: 8 servers x
# (read + occasional write) is the realistic worst case. Threads are
# explicitly named so a wedged recv is identifiable in py-spy / jstack.
_LSP_RECV_EXECUTOR: Final[ThreadPoolExecutor] = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="lsp-recv"
)

# Per-recv wall-clock budget. Past this the call is treated as a stuck
# socket: we log a warning, increment the runtime-error counter, and
# return ``b""`` so the WS proxy's pump loop terminates. 60s is generous
# enough that a healthy server idling between completions still sees a
# normal blocking recv unblock when a frame arrives, but tight enough
# that a permanent stall surfaces inside the WS keepalive window.
_LSP_RECV_TIMEOUT_S: Final[float] = 60.0


def _bump_runtime_error(language: str, error_class: LSPErrorClass) -> None:
    """Increment ``lsp_runtime_errors_total{language, error_class}``.

    Wrapped in a tiny helper so the warning sites stay symmetric and a
    future relocation of the counter (e.g. namespacing inside an
    ``LSPObservability`` class) only touches this one body. The
    ``error_class`` parameter is narrowed to :data:`LSPErrorClass` so a
    typo at any callsite is a mypy error at the source rather than a
    silently-mislabelled metric. Import is intentionally deferred so the
    sandbox module never pulls Prometheus eagerly during driver
    bootstrap (tests construct LSP wrappers without a metric registry).
    """
    try:
        from app.observability import lsp_runtime_errors_total

        lsp_runtime_errors_total.labels(language=language, error_class=error_class).inc()
    except Exception:  # pragma: no cover — telemetry must never throw
        pass


# ---------------------------------------------------------------------------
# Public language → command mapping
# ---------------------------------------------------------------------------

# Languages the platform currently spawns a language server for. The Monaco
# editor falls back to syntax highlighting on anything not in this set.
SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"python", "typescript", "go"})

# Per-language launch command. Each entry is a list[list[str]] — the WS proxy
# tries each candidate in order and uses the first whose argv[0] resolves on
# PATH (LocalSandboxDriver), or just sends the first to ``docker exec`` (the
# container image is expected to have the canonical binary).
#
# pyright (Microsoft) is preferred over python-lsp-server because the
# diagnostics it surfaces are closer to what users see in VS Code; pylsp is the
# documented fallback if a deployment can't pull in pyright for licensing
# reasons. typescript-language-server is the canonical TS server (used by both
# VS Code's `vscode.typescript-language-features` and Neovim); ``gopls serve``
# is the Go team's first-party server.
LSP_COMMANDS: Final[dict[str, list[list[str]]]] = {
    "python": [
        ["pyright-langserver", "--stdio"],
        ["pylsp"],
    ],
    "typescript": [
        ["typescript-language-server", "--stdio"],
    ],
    "go": [
        ["gopls", "serve"],
    ],
}


# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class LSPUnavailableError(RuntimeError):
    """Raised when a language server cannot be started in the sandbox.

    Carries a stable ``error_class`` discriminator so the WS proxy can serialise
    it into a structured ``{"type":"lsp_error","error":"..."}`` text frame the
    frontend can render without having to parse a free-form message. The
    discriminators today are:

    * ``binary_not_found`` — the LSP binary isn't on PATH inside the sandbox.
    * ``spawn_failed``     — the subprocess crashed during launch (rare).
    * ``unsupported_language`` — caller passed a language outside
      :data:`SUPPORTED_LANGUAGES`.
    * ``driver_unavailable`` — the Docker SDK / daemon isn't reachable.
    """

    def __init__(
        self,
        error_class: LSPErrorClass,
        language: str,
        *,
        detail: str | None = None,
    ):
        self.error_class: LSPErrorClass = error_class
        self.language = language
        self.detail = detail
        msg = f"lsp_unavailable[{error_class}] language={language}"
        if detail:
            msg = f"{msg} detail={detail}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class LSPProcess(ABC):
    """Handle to a long-lived LSP stdio process running inside the sandbox.

    Concrete subclasses bridge whatever stream the driver hands them
    (``asyncio.subprocess.Process`` for the local driver, a Docker exec socket
    for the docker driver) to the byte-oriented surface the WS proxy expects.
    """

    def __init__(self, language: str) -> None:
        self._language = language

    @property
    def language(self) -> str:
        return self._language

    @property
    @abstractmethod
    def alive(self) -> bool:
        """True iff the underlying process / socket is still readable.

        The WS proxy uses this to decide when to tear down the bidirectional
        pump (and to skip the polite ``shutdown`` notification when the
        process is already gone).
        """

    @property
    def exit_code(self) -> int | None:
        """Process exit code once the LSP has terminated, else ``None``.

        Used by the WS proxy to distinguish an OOM-killed server (exit
        code in :data:`LSP_OOM_EXIT_CODES`) from a graceful EOF. Subclasses
        override; the base returns ``None`` so a driver that genuinely
        can't read an exit status (mock LSP, custom transport) still
        gives the proxy a defined value to switch on rather than blowing
        up with ``AttributeError``.
        """
        return None

    @abstractmethod
    async def write_stdin(self, data: bytes) -> None:
        """Write ``data`` to the language server's stdin.

        Implementations MUST be re-entrant safe with concurrent
        :meth:`read_stdout` calls — the WS proxy runs the two directions in
        separate tasks. They MUST NOT raise on a clean EOF / closed pipe
        (treat it as a no-op); the proxy will notice via :attr:`alive`.
        """

    @abstractmethod
    async def read_stdout(self) -> bytes:
        """Read the next chunk from the language server's stdout.

        Returns an empty bytes object on EOF — the proxy uses ``b""`` as the
        signal to stop pumping. Concrete implementations may return any size
        chunk; the proxy does not assume frame boundaries (LSP framing is
        the FE language client's responsibility).
        """

    @abstractmethod
    async def shutdown(self, *, timeout_s: float = 2.0) -> None:
        """Tear the LSP process down.

        Implementations close stdin first to give the server a chance to
        cooperate, then wait up to ``timeout_s`` for the process to exit on
        its own, then SIGKILL. Errors are logged and swallowed — shutdown
        must never raise.
        """


# ---------------------------------------------------------------------------
# Local driver implementation
# ---------------------------------------------------------------------------


class LocalLSPProcess(LSPProcess):
    """Spawned on the host by :meth:`LocalSandboxDriver.spawn_lsp`.

    Uses ``asyncio.create_subprocess_exec`` so the read / write halves can be
    driven from the FastAPI event loop without a thread pool.
    """

    # Default read chunk — matches the PTY bridge's frame size so behaviour
    # under load is symmetric with the terminal WS.
    _CHUNK = 4096

    def __init__(
        self,
        language: str,
        proc: asyncio.subprocess.Process,
        *,
        argv: list[str],
    ) -> None:
        super().__init__(language)
        self._proc = proc
        self._argv = argv
        self._stdin_lock = asyncio.Lock()
        self._stdout_lock = asyncio.Lock()
        self._shutting_down = False

    @property
    def alive(self) -> bool:
        return self._proc.returncode is None

    @property
    def exit_code(self) -> int | None:
        """Forward ``Process.returncode``; ``None`` while still running.

        On Linux the kernel OOM killer surfaces as ``-9`` (SIGKILL) here;
        the WS proxy compares against :data:`LSP_OOM_EXIT_CODES` to
        decide between a generic ``lsp_crashed`` close and the
        structured ``lsp_oom`` frame.
        """
        return self._proc.returncode

    async def write_stdin(self, data: bytes) -> None:
        if not data:
            return
        if self._proc.stdin is None or self._proc.stdin.is_closing():
            return
        async with self._stdin_lock:
            try:
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
                # ConnectionResetError surfaces when the LSP died between our
                # alive() check and the write; RuntimeError can fire from
                # asyncio when the transport is mid-close. Either way the
                # proxy will notice via alive==False on the next read.
                # Promoted to WARNING (was DEBUG) so operators can correlate
                # a stuck pump with a broken stdin pipe; runtime counter
                # carries the per-language label for dashboarding.
                logger.warning("lsp[{}] stdin write failed: {}", self._language, exc)
                _bump_runtime_error(self._language, "stdin_broken_pipe")

    async def read_stdout(self) -> bytes:
        if self._proc.stdout is None:
            return b""
        async with self._stdout_lock:
            try:
                return await self._proc.stdout.read(self._CHUNK)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                return b""

    async def shutdown(self, *, timeout_s: float = 2.0) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True

        # 1) Close stdin so the server sees EOF and can shut down cleanly.
        try:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning("lsp[{}] stdin close failed: {}", self._language, exc)
            _bump_runtime_error(self._language, "stdin_broken_pipe")

        # 2) Give it a brief window to exit on its own.
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=timeout_s)
            return
        except TimeoutError:
            _bump_runtime_error(self._language, "shutdown_timeout")
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning("lsp[{}] wait failed: {}", self._language, exc)
            _bump_runtime_error(self._language, "shutdown_timeout")

        # 3) Escalate.
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=max(0.5, timeout_s / 2))
            return
        except TimeoutError:
            _bump_runtime_error(self._language, "terminate_failed")
        except ProcessLookupError:
            return
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning("lsp[{}] terminate failed: {}", self._language, exc)
            _bump_runtime_error(self._language, "terminate_failed")

        try:
            self._proc.kill()
        except ProcessLookupError:
            return
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning("lsp[{}] kill failed: {}", self._language, exc)
            _bump_runtime_error(self._language, "kill_failed")


async def spawn_local_lsp(
    language: str,
    *,
    cwd: str,
) -> LocalLSPProcess:
    """Resolve the LSP binary on PATH and launch it as an async subprocess.

    Raises :class:`LSPUnavailableError` with ``binary_not_found`` when no
    candidate command's argv[0] resolves on PATH — that's the expected
    dev/test environment behaviour and the WS proxy translates it into a
    structured ``lsp_error`` frame for the FE.
    """
    if language not in LSP_COMMANDS:
        raise LSPUnavailableError("unsupported_language", language)

    chosen: list[str] | None = None
    for candidate in LSP_COMMANDS[language]:
        if shutil.which(candidate[0]):
            chosen = candidate
            break
    if chosen is None:
        raise LSPUnavailableError(
            "binary_not_found",
            language,
            detail=f"none of {[c[0] for c in LSP_COMMANDS[language]]} on PATH",
        )

    # Best-effort per-language memory cap. ``resource.setrlimit(RLIMIT_AS)``
    # in a child preexec is the simplest enforcement on Linux; macOS dev
    # hosts ignore AS limits, so this is "documented intent" there. We MUST
    # NOT raise from the preexec — a misconfigured limit must never wedge a
    # working LSP spawn — so the body is wrapped and the failure mode is
    # silent fallback to no cap.
    budget_mb = LSP_MEMORY_BUDGETS_MB.get(language)
    preexec_fn = None
    if budget_mb is not None:
        budget_bytes = int(budget_mb) * 1024 * 1024

        def _set_memory_cap() -> None:  # pragma: no cover — child-only
            try:
                import resource

                resource.setrlimit(resource.RLIMIT_AS, (budget_bytes, budget_bytes))
            except Exception:
                # Silent — see the comment above. The container ceiling is
                # the backstop on hosts where the rlimit doesn't take.
                pass

        preexec_fn = _set_memory_cap

    try:
        proc = await asyncio.create_subprocess_exec(
            *chosen,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            preexec_fn=preexec_fn,
        )
    except FileNotFoundError as exc:
        # Racy disappearance between ``shutil.which`` and ``exec`` — surface
        # as the same error class so the FE handles it identically.
        raise LSPUnavailableError("binary_not_found", language, detail=str(exc)) from exc
    except OSError as exc:
        raise LSPUnavailableError("spawn_failed", language, detail=str(exc)) from exc

    logger.info(
        "lsp[{}] spawned (local) pid={} argv={}",
        language,
        proc.pid,
        chosen,
    )
    return LocalLSPProcess(language, proc, argv=chosen)


# ---------------------------------------------------------------------------
# Docker driver implementation
# ---------------------------------------------------------------------------


class DockerLSPProcess(LSPProcess):
    """LSP attached to a docker ``exec`` socket (production path).

    The Docker SDK's ``exec_create`` + ``exec_start(socket=True, demux=False)``
    returns a bidirectional socket (over ``/var/run/docker.sock``) that we
    can read/write raw bytes through. Because ``demux=False`` is set, the
    server multiplexes stdout AND stderr onto the same stream — that's fine
    because we drop stderr from the LSP at the docker side via shell
    redirection (``2>/dev/null``), so what we receive here IS the
    language-client's stdout.
    """

    _CHUNK = 4096

    def __init__(
        self,
        language: str,
        *,
        exec_id: str,
        sock: Any,
        client: Any,
        argv: list[str],
    ) -> None:
        super().__init__(language)
        self._exec_id = exec_id
        self._sock = sock
        self._client = client
        self._argv = argv
        # The underlying socket object exposes a ``_sock`` attribute on the
        # Docker SDK's ``SocketIO`` wrapper. We poke through to it so we can
        # do non-blocking reads in a thread executor; if it's already a
        # plain socket the attribute lookup falls back to the wrapper.
        self._raw = getattr(sock, "_sock", None) or sock
        self._closed = False
        # Cached exit code from the most recent ``exec_inspect`` — populated
        # lazily by ``exit_code`` so the WS proxy can read it after the read
        # pump observes EOF. Docker only fills ``ExitCode`` once
        # ``Running == False``; until then the SDK returns ``None`` and we
        # surface that as "still running".
        self._cached_exit_code: int | None = None

    @property
    def alive(self) -> bool:
        if self._closed:
            return False
        try:
            info = self._client.api.exec_inspect(self._exec_id)
            running = info.get("Running")
            if running is False:
                # Cache the exit code while we have it so ``exit_code``
                # below can answer without a second SDK round-trip.
                ec = info.get("ExitCode")
                if isinstance(ec, int):
                    self._cached_exit_code = ec
                return False
        except Exception as exc:  # pragma: no cover — best-effort
            # Promoted to WARNING (was DEBUG) so operators can correlate
            # "alive() reports stale" with the underlying Docker SDK
            # failure (daemon restart, socket closed, etc.).
            logger.warning("lsp[{}] exec_inspect failed: {}", self._language, exc)
            _bump_runtime_error(self._language, "exec_inspect_failed")
        return True

    @property
    def exit_code(self) -> int | None:
        """Return the docker ``ExitCode`` for the exec, ``None`` if running.

        Docker exposes ``ExitCode == 137`` when the container's cgroup OOM
        killer reaps the exec process (= 128 + SIGKILL). The WS proxy uses
        this to surface a structured ``lsp_oom`` frame instead of the
        generic ``lsp_crashed`` close.
        """
        if self._cached_exit_code is not None:
            return self._cached_exit_code
        try:
            info = self._client.api.exec_inspect(self._exec_id)
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning(
                "lsp[{}] exec_inspect for exit_code failed: {}",
                self._language,
                exc,
            )
            _bump_runtime_error(self._language, "exec_inspect_failed")
            return None
        if info.get("Running") is True:
            return None
        ec = info.get("ExitCode")
        if isinstance(ec, int):
            self._cached_exit_code = ec
            return ec
        return None

    async def write_stdin(self, data: bytes) -> None:
        if not data or self._closed:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._raw.send, data)
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            logger.warning("lsp[{}] socket send failed: {}", self._language, exc)
            _bump_runtime_error(self._language, "stdin_broken_pipe")
            self._closed = True

    async def read_stdout(self) -> bytes:
        if self._closed:
            return b""
        loop = asyncio.get_running_loop()
        # Bounded executor + wall-clock budget so a wedged docker socket
        # never wedges the event loop indefinitely. Default executor pool
        # is shared with the rest of the API (DB shims, FS readers etc.);
        # routing through ``_LSP_RECV_EXECUTOR`` isolates per-LSP starvation
        # and the ``wait_for`` is what actually surfaces the stall to the
        # caller as an empty read (signal-equivalent to a graceful EOF).
        try:
            data = await asyncio.wait_for(
                loop.run_in_executor(_LSP_RECV_EXECUTOR, self._raw.recv, self._CHUNK),
                timeout=_LSP_RECV_TIMEOUT_S,
            )
        except TimeoutError:
            logger.warning(
                "lsp[{}] socket recv timed out after {}s",
                self._language,
                _LSP_RECV_TIMEOUT_S,
            )
            _bump_runtime_error(self._language, "socket_recv_failed")
            self._closed = True
            return b""
        except (OSError, ConnectionResetError) as exc:
            logger.warning("lsp[{}] socket recv failed: {}", self._language, exc)
            _bump_runtime_error(self._language, "socket_recv_failed")
            self._closed = True
            return b""
        if not data:
            self._closed = True
        return data or b""

    async def shutdown(self, *, timeout_s: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        loop = asyncio.get_running_loop()

        # 1) Half-close the write side so the LSP sees EOF on stdin.
        try:
            import socket as _socket  # local import to keep module-level deps minimal

            await loop.run_in_executor(None, self._raw.shutdown, _socket.SHUT_WR)
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning(
                "lsp[{}] socket shutdown(SHUT_WR) failed: {}",
                self._language,
                exc,
            )
            _bump_runtime_error(self._language, "stdin_broken_pipe")

        # 2) Wait briefly for the exec to exit.
        deadline = asyncio.get_event_loop().time() + max(0.0, timeout_s)
        while asyncio.get_event_loop().time() < deadline:
            try:
                info = await loop.run_in_executor(
                    None, self._client.api.exec_inspect, self._exec_id
                )
                if not info.get("Running"):
                    break
            except Exception as exc:  # pragma: no cover — best-effort
                logger.warning(
                    "lsp[{}] exec_inspect during shutdown: {}",
                    self._language,
                    exc,
                )
                _bump_runtime_error(self._language, "exec_inspect_failed")
                break
            await asyncio.sleep(0.1)

        # 3) Close the socket; docker reaps the process when the exec stream
        # ends. There is no public docker SDK API for SIGKILL-ing an exec
        # PID — closing the stream is the documented escalation path.
        try:
            await loop.run_in_executor(None, self._raw.close)
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning("lsp[{}] socket close failed: {}", self._language, exc)
            _bump_runtime_error(self._language, "shutdown_timeout")


async def spawn_docker_lsp(
    container: Any,
    client: Any,
    language: str,
    *,
    workdir: str,
) -> DockerLSPProcess:
    """Launch the LSP inside ``container`` via ``docker exec`` (streaming).

    The exec is created with ``stdin=True, stdout=True, stderr=True`` and
    started in ``socket=True`` mode so we get back the bidirectional unix
    socket the SDK normally hides from ``exec_run`` callers. Stderr is
    redirected to ``/dev/null`` inside the container so the WS only carries
    the LSP's JSON-RPC stream — the server's startup chatter would otherwise
    interleave with stdout and corrupt the client's framing.
    """
    if language not in LSP_COMMANDS:
        raise LSPUnavailableError("unsupported_language", language)

    candidates = LSP_COMMANDS[language]
    if not candidates:  # pragma: no cover — defensive
        raise LSPUnavailableError("binary_not_found", language)

    # Prefer the first candidate; the container image is expected to ship it.
    # We don't probe ``which`` inside the container because that's a second
    # exec round-trip and the per-language Dockerfiles pin a known binary.
    argv = list(candidates[0])
    # ``sh -c`` lets us pipe stderr to /dev/null without parsing shell ops
    # ourselves. The argv is whitelisted (no user input ever lands here),
    # so the shell expansion is safe.
    import shlex

    quoted = " ".join(shlex.quote(part) for part in argv)

    # Per-language memory cap (P1-3 audit fix). The sandbox container's
    # 2 GiB cgroup ceiling protects the host; ``prlimit --as=`` narrows the
    # process address-space cap to the per-language budget so ONE wedged
    # server can't monopolise the whole container budget and starve a
    # second LSP (e.g. python + typescript both attached to a polyglot
    # repo). We invoke prlimit via a ``command -v`` probe so an image that
    # ships without prlimit (uncommon — util-linux is on every base we
    # use) just falls back to the container ceiling rather than failing
    # the spawn. Bytes, not megabytes: ``--as`` takes a byte count.
    budget_mb = LSP_MEMORY_BUDGETS_MB.get(language)
    if budget_mb is not None:
        budget_bytes = int(budget_mb) * 1024 * 1024
        # ``command -v prlimit`` returns 0 iff the binary is on PATH; the
        # ``&&`` chains the limited exec, the ``||`` fallback is the raw
        # exec so we never hard-fail when prlimit is missing.
        prlimited = f"prlimit --as={budget_bytes} {quoted}"
        shell_cmd = (
            f"if command -v prlimit >/dev/null 2>&1; then "
            f"exec {prlimited} 2>/dev/null; "
            f"else exec {quoted} 2>/dev/null; fi"
        )
    else:
        shell_cmd = f"exec {quoted} 2>/dev/null"

    loop = asyncio.get_running_loop()

    def _create_and_start() -> tuple[str, Any]:
        try:
            exec_info = client.api.exec_create(
                container.id,
                cmd=["/bin/sh", "-c", shell_cmd],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=False,
                workdir=workdir,
            )
            exec_id = exec_info["Id"]
            sock = client.api.exec_start(
                exec_id,
                detach=False,
                tty=False,
                stream=False,
                socket=True,
                demux=False,
            )
        except Exception as exc:
            raise LSPUnavailableError("spawn_failed", language, detail=str(exc)) from exc
        return exec_id, sock

    try:
        exec_id, sock = await loop.run_in_executor(None, _create_and_start)
    except LSPUnavailableError:
        raise
    except Exception as exc:
        # Unknown SDK failure (docker daemon vanished mid-spawn, etc.).
        raise LSPUnavailableError("driver_unavailable", language, detail=str(exc)) from exc

    logger.info(
        "lsp[{}] spawned (docker) exec_id={} argv={}",
        language,
        exec_id[:12],
        argv,
    )
    return DockerLSPProcess(language, exec_id=exec_id, sock=sock, client=client, argv=argv)


__all__ = [
    "LSP_COMMANDS",
    "LSP_MEMORY_BUDGETS_MB",
    "LSP_OOM_EXIT_CODES",
    "SUPPORTED_LANGUAGES",
    "DockerLSPProcess",
    "LSPErrorClass",
    "LSPProcess",
    "LSPUnavailableError",
    "LocalLSPProcess",
    "spawn_docker_lsp",
    "spawn_local_lsp",
]
