"""Browser ↔ container PTY bridge.

For the docker driver this attaches to a TTY exec inside the container; for
the local driver we proxy a real pty + subprocess. Auth uses the HMAC token
issued by :mod:`app.ws.auth`.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import struct
import termios
import time
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from loguru import logger

from app.ws.auth import verify_ws_token

router = APIRouter(tags=["ws"])

# Frame size for PTY reads.
_PTY_CHUNK = 4096

# Binary control-frame magic byte for terminal resize. Coordinates with the
# 4.5 frontend: ``[0x01, cols_hi, cols_lo, rows_hi, rows_lo]`` (5 bytes total).
_RESIZE_FRAME_PREFIX = 0x01
_RESIZE_FRAME_LEN = 5


def _is_control_message(data: bytes) -> dict | None:
    """Return the parsed control-frame JSON dict, or None if ``data`` is shell input.

    The FE sends a ``{"type":"ping"}`` keep-alive every ~20s. Without this
    decoder the bytes would land on the PTY and corrupt the shell prompt.
    Only short, JSON-shaped frames are considered — anything that doesn't
    parse cleanly as a dict is forwarded to the shell verbatim.
    """
    if not data or len(data) > 256 or data[:1] not in (b"{", b" ", b"\t"):
        return None
    try:
        text = data.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("type"), str):
        return parsed
    return None


def _apply_resize(pty_fd: int, data: bytes) -> bool:
    """Try to interpret ``data`` as a resize control frame.

    Returns True if the frame was consumed (ioctl applied or attempted).
    Returns False if the data is regular PTY input.
    """
    if len(data) != _RESIZE_FRAME_LEN or data[0] != _RESIZE_FRAME_PREFIX:
        return False
    try:
        cols = struct.unpack(">H", data[1:3])[0]
        rows = struct.unpack(">H", data[3:5])[0]
    except struct.error:
        return False
    if cols == 0 or rows == 0:
        # Treat zeros as a noop rather than blowing away the window size.
        return True
    try:
        fcntl.ioctl(pty_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError as exc:
        logger.debug("resize ioctl failed: {}", exc)
    return True


@router.websocket("/ws/sessions/{session_id}/terminal")
async def terminal_ws(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(""),
):
    sid_str = str(session_id)
    if not verify_ws_token(token, sid_str):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="bad token")
        return

    # Belt-and-braces ownership check: the WS token was originally minted by a
    # cookie-authenticated REST call (``/ws-token`` requires ``_require_owned_session``).
    # If the session has since been deleted / abandoned we refuse the upgrade
    # so a stolen-but-valid token can't be reused after the session lifecycle
    # ended. Cookie-based ownership re-check is not possible over WS upgrade.
    if not await _session_exists(session_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="session not found")
        return

    await websocket.accept()
    pool = getattr(websocket.app.state, "sandbox_pool", None)

    # Provisioning is asynchronous — the terminal can connect a beat before the
    # handle lands on the pool. Poll briefly so the UI shows "Connecting…" once
    # rather than "no_sandbox" + retry.
    handle = None
    if pool is not None:
        for _ in range(30):  # ~6s @ 200ms
            for h in pool.handles_snapshot():
                if h.session_id == session_id:
                    handle = h
                    break
            if handle is not None:
                break
            await asyncio.sleep(0.2)

    if handle is None or pool is None:
        await websocket.send_json(
            {"type": "error", "code": "no_sandbox", "detail": "session has no sandbox"}
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    try:
        attach = await pool.driver.attach_shell(handle)
    except Exception as exc:
        logger.warning("attach_shell failed for {}: {}", session_id, exc)
        await websocket.send_json({"type": "error", "code": "attach_failed", "detail": str(exc)})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # Local driver returns (pty_fd, proc, ptyid); docker returns (exec_id, socket).
    if pool.driver.name == "local":
        await _bridge_local_pty(websocket, attach, pool.driver, handle)
    else:
        await _bridge_docker_socket(websocket, attach)


async def _bridge_local_pty(websocket: WebSocket, attach, driver, handle) -> None:  # noqa: PLR0915
    pty_fd, proc, ptyid = attach
    loop = asyncio.get_running_loop()
    closed = asyncio.Event()

    async def reader_task() -> None:
        try:
            while not closed.is_set():
                data = await loop.run_in_executor(None, _read_fd, pty_fd, _PTY_CHUNK)
                if not data:
                    break
                await websocket.send_bytes(data)
        except (OSError, WebSocketDisconnect):
            pass
        finally:
            closed.set()

    async def writer_task() -> None:
        try:
            while not closed.is_set():
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes") or (msg.get("text") or "").encode("utf-8")
                if not data:
                    continue
                # First, try to interpret the bytes as a resize control frame.
                if _apply_resize(pty_fd, data):
                    continue
                # JSON keep-alive ping from the FE — respond with pong and
                # never forward to the PTY (would corrupt the shell prompt).
                ctrl = _is_control_message(data)
                if ctrl is not None and ctrl.get("type") == "ping":
                    try:
                        await websocket.send_json({"type": "pong", "ts": int(time.time())})
                    except Exception:  # pragma: no cover — best-effort
                        pass
                    continue
                if ctrl is not None:
                    # Recognised control frame but not one we act on (e.g.
                    # pong). Swallow rather than forward to the PTY.
                    continue
                # Run the PTY write off the event loop. A full PTY buffer
                # (slow consumer in the container) would otherwise block the
                # entire asyncio loop on ``os.write`` — every other request
                # served by this worker would stall until the PTY drains.
                try:
                    await loop.run_in_executor(None, os.write, pty_fd, data)
                except OSError as exc:
                    # Broken pipe / closed PTY — tear the bridge down cleanly.
                    logger.debug("pty write failed: {}", exc)
                    break
        except (OSError, WebSocketDisconnect) as exc:
            # ``OSError`` covers BrokenPipeError, ConnectionResetError, etc.
            # surfaced from ``websocket.receive`` itself when the underlying
            # socket dies abruptly.
            logger.debug("terminal writer task ended: {}", exc)
        finally:
            closed.set()

    # Spawn each direction as a tracked task so when one finishes (clean
    # close, broken PTY, etc.) we explicitly cancel the other instead of
    # leaving it blocked on ``websocket.receive`` indefinitely. The
    # shared ``closed`` event coordinates a graceful stop; the cancel is
    # the safety net for half-closed sockets that never trip the event
    # naturally (manifested as a PTY fd leak under disconnect churn).
    reader = asyncio.create_task(reader_task())
    writer = asyncio.create_task(writer_task())
    try:
        _done, pending = await asyncio.wait(
            {reader, writer}, return_when=asyncio.FIRST_COMPLETED
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

    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    # Close only this tab's PTY; sibling tabs on the same handle stay alive.
    try:
        driver.close_pty(handle, ptyid)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("close_pty failed: {}", exc)


async def _bridge_docker_socket(websocket: WebSocket, attach) -> None:
    _exec_id, sock = attach
    loop = asyncio.get_running_loop()
    closed = asyncio.Event()

    raw = getattr(sock, "_sock", None) or sock

    async def reader_task() -> None:
        try:
            while not closed.is_set():
                data = await loop.run_in_executor(None, raw.recv, _PTY_CHUNK)
                if not data:
                    break
                await websocket.send_bytes(data)
        except (OSError, WebSocketDisconnect):
            pass
        finally:
            closed.set()

    async def writer_task() -> None:
        try:
            while not closed.is_set():
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes") or (msg.get("text") or "").encode("utf-8")
                if not data:
                    continue
                # JSON keep-alive ping from the FE — respond with pong and
                # don't forward to the docker exec socket.
                ctrl = _is_control_message(data)
                if ctrl is not None and ctrl.get("type") == "ping":
                    try:
                        await websocket.send_json({"type": "pong", "ts": int(time.time())})
                    except Exception:  # pragma: no cover — best-effort
                        pass
                    continue
                if ctrl is not None:
                    continue
                await loop.run_in_executor(None, raw.send, data)
        except WebSocketDisconnect:
            pass
        finally:
            closed.set()

    reader = asyncio.create_task(reader_task())
    writer = asyncio.create_task(writer_task())
    try:
        _done, pending = await asyncio.wait(
            {reader, writer}, return_when=asyncio.FIRST_COMPLETED
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
    try:
        raw.close()
    except OSError:
        pass


def _read_fd(fd: int, n: int) -> bytes:
    try:
        return os.read(fd, n)
    except OSError:
        return b""


async def _session_exists(session_id: uuid.UUID) -> bool:
    """Return True when ``session_id`` resolves to a non-deleted row.

    Used as a belt-and-braces ownership check on WS upgrade — see
    :func:`terminal_ws`. Returns True on DB error so a transient outage
    doesn't lock out legitimate users.
    """
    try:
        from sqlalchemy import select

        from app.db.session import AsyncSessionLocal
        from app.models.session import SessionRow

        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(select(SessionRow.id).where(SessionRow.id == session_id))
            ).first()
            return row is not None
    except Exception as exc:  # pragma: no cover — defensive, no lockout
        logger.debug("session existence check failed for {}: {}", session_id, exc)
        return True
