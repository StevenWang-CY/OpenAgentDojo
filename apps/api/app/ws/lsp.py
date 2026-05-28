"""LSP (Language Server Protocol) WebSocket proxy (P1-3).

The browser opens a WebSocket against this endpoint; ``monaco-languageclient``
on the frontend speaks LSP JSON-RPC over the open socket. This module is a
*byte-faithful pump* between the WS and the language-server stdio inside the
session's sandbox — it never parses JSON-RPC, never caches anything, and
never spawns more than one LSP per ``(session, language)`` pair.

Single-worker / sticky-session assumption
-----------------------------------------

The in-process :data:`_active_lsp` registry below is intentionally
process-local. It enforces "one LSP per (session, language)" without
coordinating across replicas, which is correct iff:

* the API runs with ``WEB_CONCURRENCY == 1`` (one uvicorn worker), OR
* the load balancer pins a session's WS upgrades to the same worker
  (sticky session by session_id).

Running multiple workers without sticky sessions breaks the dedup
guarantee — two upgrades for the same ``(session, language)`` could
land on different workers, and each would happily spawn its own LSP
inside the sandbox container. We log a WARNING from the route handler
when ``WEB_CONCURRENCY != "1"`` so operators see the regression at
first use rather than at the next P3 audit. Fixing this properly means
storing the registry in Redis (keyed by ``(session_id, language)``,
with a TTL that aligns to the LSP heartbeat); P1 deliberately ships the
process-local cut and defers the distributed registry to a follow-on.

Architecture (mirrors :mod:`app.ws.terminal`)
---------------------------------------------

* Auth: short-lived HMAC token validated via :func:`app.ws.auth.verify_ws_token`,
  PLUS an ``Origin`` allow-list check via :func:`app.ws.auth.is_allowed_origin`
  (matches ``settings.cors_origins``; ``*`` = dev mode).
* Lifecycle:
    1. Verify the session exists and is in ``active`` status.
    2. Resolve the sandbox handle from :class:`app.sandbox.pool.SandboxPool`.
    3. Refuse the upgrade if an exclusive mutation (apply-patch) is in
       flight on the handle — 4503 / ``sandbox_busy``.
    4. Spawn an LSP via ``driver.spawn_lsp(handle, language)`` — bound to
       one per ``(session_id, language)``; a second WS attempt for the same
       pair is closed with code 4409 / reason ``lsp_already_running``.
    5. Run two bidirectional pump tasks (WS↔LSP). Bytes are forwarded raw.
    6. Tear everything down on either side's close. An exit code in
       :data:`app.sandbox.lsp.LSP_OOM_EXIT_CODES` upgrades the close to
       ``lsp_oom`` (structured frame + 4503) so the FE can distinguish a
       memory-cap reap from a generic crash.
* Errors:
    * :class:`app.sandbox.lsp.LSPUnavailableError` → emit one structured
      ``lsp_error`` text frame, increment ``lsp_errors_total``, close.
    * Unknown session → 4404 / ``session_not_found``.
    * Inactive session → 4404 / ``session_not_active``.
    * Sandbox mid-mutation → 4503 / ``sandbox_busy``.
    * Origin not on allow-list → 4403 / ``origin_forbidden``.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from loguru import logger
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.session import SessionRow
from app.observability import lsp_errors_total, lsp_sessions_started_total
from app.sandbox.lsp import (
    LSP_OOM_EXIT_CODES,
    SUPPORTED_LANGUAGES,
    LSPErrorClass,
    LSPProcess,
    LSPUnavailableError,
)
from app.schemas.lsp import LSPErrorFrame
from app.ws.auth import is_allowed_origin, verify_ws_token

router = APIRouter(tags=["ws"])

# WebSocket subprotocol the FE must request on upgrade. Bumping the suffix
# breaks old clients deliberately; today there's only ``v1``.
_LSP_SUBPROTOCOL = "lsp.openagentdojo.v1"

# Close codes — 4xxx are the application-defined range Starlette accepts.
_WS_CLOSE_SESSION_NOT_FOUND = 4404
_WS_CLOSE_SESSION_NOT_ACTIVE = 4404  # same code, different reason string
_WS_CLOSE_LSP_ALREADY_RUNNING = 4409
_WS_CLOSE_LSP_UNAVAILABLE = 4503
_WS_CLOSE_SANDBOX_BUSY = 4503  # same as unavailable; distinct error_class
_WS_CLOSE_ORIGIN_FORBIDDEN = 4403


# One-shot guard so the multi-worker warning logs once per process, not on
# every WS upgrade. The registry assumption only matters if it's violated;
# a single log line at first use is enough to alert ops.
_WEB_CONCURRENCY_WARNED = False


def _maybe_warn_multi_worker() -> None:
    """Log a one-shot warning if the process looks like it's running multi-worker.

    See the module docstring for why the in-process LSP registry breaks
    under ``WEB_CONCURRENCY > 1`` without sticky sessions. We can't
    *assert* here (the API may legitimately run multi-worker for the
    REST surface and just accept the LSP soft-degradation), so the
    posture is: warn loudly once, let ops decide.
    """
    global _WEB_CONCURRENCY_WARNED
    if _WEB_CONCURRENCY_WARNED:
        return
    _WEB_CONCURRENCY_WARNED = True
    workers = (os.environ.get("WEB_CONCURRENCY") or "1").strip()
    if workers != "1":
        logger.warning(
            "lsp ws: WEB_CONCURRENCY={!r} but the LSP registry is "
            "process-local; the 'one LSP per (session, language)' "
            "invariant only holds with sticky sessions or workers=1. "
            "See app.ws.lsp module docstring.",
            workers,
        )

# Bytes per pump-loop read from the WS side. Generous — JSON-RPC LSP frames
# can easily run to several KB when the server returns a completion list
# with rich documentation strings.
_WS_RECV_CHUNK = 65_536


# Max age (seconds) a ``_PendingLSP`` placeholder may sit in the registry
# before the next attempt evicts it as stale. ``spawn_lsp`` historically
# completes inside ~2s (LocalSandboxDriver) and ~5s (DockerSandboxDriver
# with a warm image). 30s is the conservative ceiling — past that the
# spawning task has almost certainly died without releasing the slot,
# and the next client should be allowed to take over.
_PENDING_LSP_MAX_AGE_S: float = 30.0


# In-process registry of currently-attached LSPs, keyed by
# ``(session_id, language)``. Used to enforce "one LSP per language per
# session" without a Redis round-trip. The single-process assumption is OK
# because the FE only ever opens one WS per (session, language); a cross-
# replica race would just close one of the two connections with 4409 and
# the FE retries.
#
# Values are either a live :class:`LSPProcess` *or* a :class:`_PendingLSP`
# sentinel placed during the ``spawn_lsp`` in-flight window (so a second
# concurrent upgrade observes the slot as taken). Callers MUST switch on
# ``isinstance(entry, _PendingLSP)`` before touching the duck-typed
# placeholder.
_active_lsp: dict[tuple[uuid.UUID, str], LSPProcess | _PendingLSP] = {}
_active_lsp_lock = asyncio.Lock()


def _coerce_language(raw: str) -> str | None:
    """Validate the ``?language=`` query value against the supported set."""
    lang = (raw or "").strip().lower()
    if lang in SUPPORTED_LANGUAGES:
        return lang
    return None


async def _session_status(session_id: uuid.UUID) -> str | None:
    """Return the session's ``status`` column, or ``None`` if it doesn't exist.

    Fails CLOSED on DB error so a transient outage briefly rejects valid
    clients — same posture as :mod:`app.ws.events`. The WS token is short-
    lived so the FE retries cheaply.
    """
    try:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(select(SessionRow.status).where(SessionRow.id == session_id))
            ).first()
            return None if row is None else str(row[0])
    except Exception as exc:  # pragma: no cover — defensive, fail closed
        logger.warning("lsp ws: session status lookup failed for {}: {}", session_id, exc)
        return None


async def _send_lsp_error_and_close(
    websocket: WebSocket,
    *,
    language: str,
    error: LSPErrorClass,
    detail: str | None,
    close_code: int,
) -> None:
    """Emit the structured ``lsp_error`` frame and close the socket.

    The frame schema is :class:`app.schemas.lsp.LSPErrorFrame`. We send it
    as JSON-encoded text (not bytes) so the FE's discriminated union
    narrowing on ``type === "lsp_error"`` works regardless of how the
    JSON-RPC byte stream below is framed.
    """
    frame = LSPErrorFrame(type="lsp_error", error=error, language=language, detail=detail)
    try:
        await websocket.send_text(frame.model_dump_json())
    except Exception as exc:  # pragma: no cover — best-effort
        logger.debug("lsp ws: send_text(lsp_error) failed: {}", exc)
    try:
        await websocket.close(code=close_code, reason=error[:120])
    except Exception as exc:  # pragma: no cover — best-effort
        logger.debug("lsp ws: close failed: {}", exc)


async def _pump_ws_to_lsp(
    websocket: WebSocket,
    lsp: LSPProcess,
    closed: asyncio.Event,
) -> None:
    """FE → LSP. Forward every binary/text frame to the LSP stdin verbatim."""
    try:
        while not closed.is_set():
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is None:
                text = msg.get("text") or ""
                data = text.encode("utf-8") if text else b""
            if not data:
                continue
            await lsp.write_stdin(data)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("lsp ws → lsp pump ended: {}", exc)
    finally:
        closed.set()


async def _pump_lsp_to_ws(
    websocket: WebSocket,
    lsp: LSPProcess,
    closed: asyncio.Event,
    crashed_flag: list[bool],
) -> None:
    """LSP → FE. Forward every stdout chunk as a binary WS frame.

    ``crashed_flag`` is a length-1 list used as a mutable out-param: the
    pump sets ``crashed_flag[0] = True`` if it terminates because the
    underlying LSP returned EOF (i.e. exited unexpectedly) while the WS
    side was still healthy. The lifecycle code reads this back and emits
    a ``1011 lsp_crashed`` close on the socket so the FE distinguishes
    "user navigated away" from "language server died".
    """
    try:
        while not closed.is_set():
            data = await lsp.read_stdout()
            if not data:
                # Empty read = EOF from the language server. If the WS
                # side hasn't already requested a close we treat this as
                # a crash: a healthy LSP only returns ``b""`` after the
                # WS proxy explicitly shut it down via :meth:`shutdown`,
                # which only happens once ``closed`` is set.
                if not closed.is_set():
                    crashed_flag[0] = True
                break
            try:
                await websocket.send_bytes(data)
            except WebSocketDisconnect:
                break
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("lsp ← ws pump ended: {}", exc)
    finally:
        closed.set()


@router.websocket("/ws/sessions/{session_id}/lsp")
async def lsp_ws(  # noqa: PLR0912,PLR0915 — sequential lifecycle is easier to read in one body
    websocket: WebSocket,
    session_id: uuid.UUID,
    language: str = Query(..., description="LSP language: python|typescript|go"),
    token: str = Query("", description="HMAC ws-token bound to the session"),
) -> None:
    """WS proxy that pumps JSON-RPC bytes between Monaco and the sandbox LSP."""
    sid_str = str(session_id)

    # One-shot diagnostic so a misconfigured deployment (multi-worker without
    # sticky sessions) surfaces in the API logs the first time someone tries
    # to attach an LSP. See module docstring for the full reasoning.
    _maybe_warn_multi_worker()

    # 0) Origin allow-list. Closes BEFORE accept so a malicious cross-site
    # WS upgrade never even sees the handshake completed. Browsers always
    # ship Origin; non-browser clients (no Origin header) pass through to
    # the token check below per the shared helper's contract.
    if not is_allowed_origin(websocket):
        try:
            # Try to surface a structured frame too, but the socket
            # hasn't been accepted yet — Starlette doesn't allow a text
            # frame before ``accept``. Close-with-reason is what
            # downstream tooling can observe.
            await websocket.close(
                code=_WS_CLOSE_ORIGIN_FORBIDDEN, reason="origin_forbidden"
            )
        except Exception as exc:  # pragma: no cover — best-effort
            logger.debug("lsp ws: origin-forbidden close failed: {}", exc)
        lsp_errors_total.labels(
            language=(language or "unknown"), error_class="origin_forbidden"
        ).inc()
        return

    # 1) Auth. Bad token → 1008 (policy violation) per the terminal WS contract.
    if not verify_ws_token(token, sid_str):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="bad token")
        return

    # 2) Language validation. We do this BEFORE accepting so a misconfigured
    # FE never enters the JSON-RPC pump loop with an unsupported language.
    norm_lang = _coerce_language(language)
    if norm_lang is None:
        lsp_errors_total.labels(language=language or "unknown", error_class="unsupported_language").inc()
        await websocket.close(code=4400, reason="unsupported_language")
        return

    # 3) Session existence + active-status check. We accept the WS only
    # after these gates pass so the FE sees a deterministic close code.
    sess_status = await _session_status(session_id)
    if sess_status is None:
        lsp_errors_total.labels(language=norm_lang, error_class="session_not_found").inc()
        await websocket.close(code=_WS_CLOSE_SESSION_NOT_FOUND, reason="session_not_found")
        return
    if sess_status != "active":
        lsp_errors_total.labels(language=norm_lang, error_class="session_not_active").inc()
        await websocket.close(code=_WS_CLOSE_SESSION_NOT_ACTIVE, reason="session_not_active")
        return

    # 4) Accept the subprotocol the FE requested. Starlette echoes it on the
    # upgrade response so monaco-languageclient sees the negotiation succeed.
    requested = websocket.scope.get("subprotocols") or []
    chosen = _LSP_SUBPROTOCOL if _LSP_SUBPROTOCOL in requested else None
    await websocket.accept(subprotocol=chosen)

    # 5) Resolve the sandbox handle from the pool. Brief poll mirrors the
    # terminal WS — the user can hit the LSP WS a beat before provision
    # registers the handle.
    pool = getattr(websocket.app.state, "sandbox_pool", None)
    handle = None
    if pool is not None:
        for _ in range(30):  # ~6s @ 200ms
            if hasattr(pool, "handle_for"):
                handle = pool.handle_for(session_id)
            if handle is None:
                for h in pool.handles_snapshot():
                    if h.session_id == session_id:
                        handle = h
                        break
            if handle is not None:
                break
            await asyncio.sleep(0.2)

    if handle is None or pool is None:
        await _send_lsp_error_and_close(
            websocket,
            language=norm_lang,
            error="no_sandbox",
            detail="session has no sandbox attached",
            close_code=_WS_CLOSE_LSP_UNAVAILABLE,
        )
        lsp_errors_total.labels(language=norm_lang, error_class="no_sandbox").inc()
        return

    # 5.5) Sandbox busy check (P1-3 audit fix).
    #
    # If an apply-patch (or any other exclusive mutation) is in flight on
    # this handle, we MUST refuse the LSP attach: the patch is rewriting
    # workspace files underneath us, and spawning a long-lived stdio
    # process here would either race the writes (LSP indexes a
    # half-applied tree) or extend the apply-patch lock window
    # unpredictably. The FE retries on close-code 4503 with backoff, so
    # the user-visible effect is just "LSP cold-starts a beat later".
    is_busy_fn = getattr(pool, "is_busy", None)
    if callable(is_busy_fn):
        try:
            busy = bool(is_busy_fn(handle))
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("lsp ws: pool.is_busy raised: {}", exc)
            busy = False
        if busy:
            await _send_lsp_error_and_close(
                websocket,
                language=norm_lang,
                error="sandbox_busy",
                detail="apply-patch in flight; retry after the patch settles",
                close_code=_WS_CLOSE_SANDBOX_BUSY,
            )
            lsp_errors_total.labels(
                language=norm_lang, error_class="sandbox_busy"
            ).inc()
            return

    # 6) Single-LSP-per-(session,language) registry. The lock guards the
    # check-then-insert race between two concurrent upgrades for the same
    # pair (FE retry storm, browser tab duplication, etc.). The losing
    # connection gets 4409 and the FE's monaco-languageclient handles the
    # retry with backoff.
    #
    # Liveness check (P4.1 audit fix): an entry whose underlying LSP has
    # already died (or whose ``_PendingLSP`` placeholder has been parked
    # past ``_PENDING_LSP_MAX_AGE_S``) is evicted before the dedup branch
    # fires. Without this, a crashed-but-still-registered process would
    # permanently lock the slot — the FE would see ``lsp_already_running``
    # for the rest of the session.
    key = (session_id, norm_lang)
    async with _active_lsp_lock:
        existing = _active_lsp.get(key)
        if existing is not None:
            evict = False
            if isinstance(existing, _PendingLSP):
                if existing.age_seconds() > _PENDING_LSP_MAX_AGE_S:
                    logger.warning(
                        "lsp[{}] evicting stale placeholder (age={:.1f}s) for session={}",
                        norm_lang,
                        existing.age_seconds(),
                        session_id,
                    )
                    evict = True
            # Real LSPProcess. Forward the duck-typed ``alive`` flag.
            elif not existing.alive:
                logger.warning(
                    "lsp[{}] evicting dead registry entry for session={}",
                    norm_lang,
                    session_id,
                )
                evict = True
            if evict:
                _active_lsp.pop(key, None)
                lsp_errors_total.labels(
                    language=norm_lang, error_class="dead_entry_evicted"
                ).inc()
                existing = None

        if existing is not None:
            await _send_lsp_error_and_close(
                websocket,
                language=norm_lang,
                error="lsp_already_running",
                detail="another LSP for this language is already attached",
                close_code=_WS_CLOSE_LSP_ALREADY_RUNNING,
            )
            lsp_errors_total.labels(language=norm_lang, error_class="lsp_already_running").inc()
            return
        # Placeholder so a second concurrent upgrade sees the slot as taken
        # even while spawn_lsp is still in flight. We replace it with the
        # real handle below.
        _active_lsp[key] = _PendingLSP(norm_lang)

    lsp: LSPProcess | None = None
    try:
        try:
            lsp = await pool.driver.spawn_lsp(handle, norm_lang)
        except LSPUnavailableError as exc:
            await _send_lsp_error_and_close(
                websocket,
                language=norm_lang,
                error=exc.error_class,
                detail=exc.detail,
                close_code=_WS_CLOSE_LSP_UNAVAILABLE,
            )
            lsp_errors_total.labels(language=norm_lang, error_class=exc.error_class).inc()
            return
        except Exception as exc:  # pragma: no cover — defensive
            logger.opt(exception=True).warning(
                "lsp[{}] spawn failed (unexpected): {}", norm_lang, exc
            )
            await _send_lsp_error_and_close(
                websocket,
                language=norm_lang,
                error="spawn_failed",
                detail=str(exc),
                close_code=_WS_CLOSE_LSP_UNAVAILABLE,
            )
            lsp_errors_total.labels(language=norm_lang, error_class="spawn_failed").inc()
            return

        # Replace the placeholder with the real handle so the registry's
        # ``alive`` semantics match what the second-WS-attempt path checks.
        async with _active_lsp_lock:
            _active_lsp[key] = lsp

        lsp_sessions_started_total.labels(language=norm_lang).inc()
        logger.info(
            "lsp[{}] ws attached session={} driver={}",
            norm_lang,
            session_id,
            pool.driver.name,
        )

        # 7) Drive the bidirectional pump until either side stops. The
        # ``closed`` event coordinates a graceful stop; the explicit cancel
        # of pending tasks is the safety net for half-closed sockets.
        # ``crashed_flag`` is a length-1 list (poor-man's mutable
        # reference) shared with the LSP→WS pump so the tear-down branch
        # below can emit a ``1011 lsp_crashed`` close when the LSP exited
        # unexpectedly while the WS side was still healthy.
        closed = asyncio.Event()
        crashed_flag: list[bool] = [False]
        ws_to_lsp = asyncio.create_task(
            _pump_ws_to_lsp(websocket, lsp, closed), name=f"lsp-ws-to-lsp[{norm_lang}]"
        )
        lsp_to_ws = asyncio.create_task(
            _pump_lsp_to_ws(websocket, lsp, closed, crashed_flag),
            name=f"lsp-lsp-to-ws[{norm_lang}]",
        )
        try:
            _done, pending = await asyncio.wait(
                {ws_to_lsp, lsp_to_ws}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            closed.set()
    finally:
        # 8) Tear-down: shut down the LSP (if we got one) and free the slot.
        if lsp is not None:
            try:
                await lsp.shutdown(timeout_s=2.0)
            except Exception as exc:  # pragma: no cover — best-effort
                logger.debug("lsp[{}] shutdown raised: {}", norm_lang, exc)

        async with _active_lsp_lock:
            registered = _active_lsp.get(key)
            # Only pop if we still own the slot — paranoia against future
            # refactors that hand the slot off mid-flight.
            if registered is lsp or isinstance(registered, _PendingLSP):
                _active_lsp.pop(key, None)

        # If the LSP died mid-flight while the WS was still healthy,
        # close with a deterministic 1011 + reason so the FE can
        # distinguish "user navigated away" from "language server
        # crashed". The flag is only set by the LSP→WS pump when it
        # observed EOF without a prior shutdown request.
        crashed = bool(crashed_flag[0]) if "crashed_flag" in locals() else False
        if crashed:
            # OOM-killer detection: if the process exited with one of the
            # cgroup-OOM exit codes (137 / -9 / 9), upgrade the close to a
            # structured ``lsp_oom`` frame so the FE can show
            # "memory cap hit, falling back to syntax-only" instead of the
            # generic "language server crashed" copy. We read the cached
            # exit code off the LSPProcess subclass (Docker caches via
            # ``exec_inspect``, Local forwards ``Process.returncode``).
            exit_code: int | None = None
            if lsp is not None:
                try:
                    exit_code = lsp.exit_code
                except Exception as exc:  # pragma: no cover — defensive
                    logger.debug(
                        "lsp[{}] exit_code lookup failed: {}", norm_lang, exc
                    )

            if exit_code is not None and exit_code in LSP_OOM_EXIT_CODES:
                lsp_errors_total.labels(
                    language=norm_lang, error_class="lsp_oom"
                ).inc()
                # Best-effort: emit the structured frame BEFORE the close
                # so the FE's discriminated-union narrowing has something
                # to switch on. The frame may be dropped if the WS is
                # already half-closed; that's fine — the close reason
                # below carries the same signal.
                try:
                    frame = LSPErrorFrame(
                        type="lsp_error",
                        error="lsp_oom",
                        language=norm_lang,
                        detail=f"language server reaped by OOM killer (exit={exit_code})",
                    )
                    await websocket.send_text(frame.model_dump_json())
                except Exception as exc:  # pragma: no cover — best-effort
                    logger.debug(
                        "lsp[{}] lsp_oom frame send failed: {}", norm_lang, exc
                    )
                try:
                    await websocket.close(
                        code=_WS_CLOSE_LSP_UNAVAILABLE, reason="lsp_oom"
                    )
                except Exception:  # pragma: no cover — best-effort
                    pass
            else:
                lsp_errors_total.labels(
                    language=norm_lang, error_class="lsp_crashed"
                ).inc()
                try:
                    # ``client_state`` / ``application_state`` aren't part of
                    # the public WebSocket surface in older Starlettes; the
                    # close call below is itself idempotent so we just try
                    # and swallow the AttributeError + ConnectionClosed.
                    await websocket.close(code=1011, reason="lsp_crashed")
                except Exception:  # pragma: no cover — best-effort
                    pass
        else:
            try:
                # Idempotent — Starlette tracks WS state internally.
                await websocket.close()
            except Exception:  # pragma: no cover — already closed paths
                pass


class _PendingLSP:
    """Sentinel inserted into the registry while ``spawn_lsp`` is in flight.

    Intentionally NOT a subclass of :class:`LSPProcess` — earlier revisions
    inherited from the ABC, which made the sentinel structurally
    indistinguishable from a real driver handle and forced every reader
    (``shutdown``, ``alive``, the pump loops) to defensively guard against
    a half-initialised LSP. The right shape is a plain duck-typed marker:
    the registry's value-type is ``LSPProcess | _PendingLSP`` and callers
    use ``isinstance`` to switch.

    Carries the language label (purely for log lines) and a creation
    timestamp so the dedup branch can age out a stale placeholder when a
    spawning task dies without unregistering itself.
    """

    __slots__ = ("_created_at", "language")

    def __init__(self, language: str) -> None:
        self.language = language
        self._created_at = time.monotonic()

    def age_seconds(self) -> float:
        """Wall-clock age of the placeholder, in seconds."""
        return max(0.0, time.monotonic() - self._created_at)
