"""P1-3 audit — LSP WS surfaces a structured ``lsp_oom`` frame on OOM-killed exits.

When the language server's process exits with one of
:data:`app.sandbox.lsp.LSP_OOM_EXIT_CODES` (137 from docker / -9 from
``Process.returncode``), the WS proxy MUST:

* emit one :class:`LSPErrorFrame` with ``error="lsp_oom"``,
* close with code 4503 + reason ``lsp_oom``.

This is distinct from the generic ``lsp_crashed`` (1011) close used
when the process EOFs without an OOM signature — the FE distinguishes
the two via the discriminated union and can show a "memory cap hit,
falling back to syntax-only" hint instead of a generic error toast.

The test uses a fake :class:`LSPProcess` that simulates an OOM by
returning ``b""`` from ``read_stdout`` (the EOF sentinel the proxy's
pump consumes) and reporting ``exit_code == 137`` afterwards.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.sandbox.lsp import LSPProcess
from app.sandbox.types import SandboxHandle


class _OOMLSP(LSPProcess):
    """LSP that immediately EOFs and reports a docker-OOM exit code.

    The proxy's lifecycle:
      1. spawn_lsp returns this handle.
      2. The LSP→WS pump calls ``read_stdout`` which returns ``b""``.
      3. The pump sets ``crashed_flag[0] = True`` because the WS side
         hadn't requested a shutdown yet.
      4. The tear-down branch reads ``exit_code``, sees 137, upgrades
         the close to a structured ``lsp_oom`` frame + 4503.
    """

    def __init__(self, language: str = "python") -> None:
        super().__init__(language)
        self._alive = True
        self._yielded_eof = False

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def exit_code(self) -> int | None:
        # Once we've reported EOF, return the OOM signature.
        return 137 if self._yielded_eof else None

    async def write_stdin(self, data: bytes) -> None:
        return

    async def read_stdout(self) -> bytes:
        # First call returns b"" → proxy treats as crash.
        if not self._yielded_eof:
            self._yielded_eof = True
            self._alive = False
            return b""
        # Subsequent calls block briefly so we don't busy-loop.
        await asyncio.sleep(0.05)
        return b""

    async def shutdown(self, *, timeout_s: float = 2.0) -> None:
        self._alive = False


class _FakeDriver:
    name = "local"

    async def spawn_lsp(self, _handle, language: str) -> _OOMLSP:
        return _OOMLSP(language)


class _FakePool:
    def __init__(self, handle: SandboxHandle) -> None:
        self._handle = handle
        self.driver = _FakeDriver()

    def handle_for(self, session_id: uuid.UUID) -> SandboxHandle | None:
        return self._handle if session_id == self._handle.session_id else None

    def handles_snapshot(self) -> list[SandboxHandle]:
        return [self._handle]

    def is_busy(self, _handle: SandboxHandle) -> bool:
        return False


def _disable_route_gates(monkeypatch) -> None:
    from app.ws import lsp as lsp_ws

    monkeypatch.setattr(lsp_ws, "verify_ws_token", lambda *_a, **_kw: True)
    monkeypatch.setattr(lsp_ws, "is_allowed_origin", lambda *_a, **_kw: True)

    async def _fake_status(_sid):
        return "active"

    monkeypatch.setattr(lsp_ws, "_session_status", _fake_status)


def test_oom_exit_emits_lsp_oom_frame(monkeypatch) -> None:
    """An LSP that exits with code 137 closes with ``lsp_oom`` not ``lsp_crashed``.

    Reading after the close raises :class:`WebSocketDisconnect`; the
    close code on the exception must be 4503 (the design's OOM-specific
    code). The structured frame is sent before the close, but
    Starlette's TestClient may deliver them in either order depending
    on how quickly the pump terminates — we accept either path as long
    as one of the two reads surfaces ``lsp_oom``.
    """
    from app.main import create_app
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()
    _disable_route_gates(monkeypatch)

    app = create_app()
    session_id = uuid.uuid4()
    handle = SandboxHandle(
        id=f"handle-{session_id}",
        driver="local",
        workdir=Path("/tmp/lsp-oom-test"),
        mission_id="mission-test",
        session_id=session_id,
    )

    with TestClient(app) as client:
        app.state.sandbox_pool = _FakePool(handle)
        url = f"/ws/sessions/{session_id}/lsp?language=python&token=anything"

        observed_oom = False
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                url, subprotocols=["lsp.openagentdojo.v1"]
            ) as ws:
                # The LSP returns EOF immediately; pump terminates and
                # the proxy enters the OOM branch. Read up to a few
                # frames in case Starlette interleaves the close
                # notification with the structured frame.
                for _ in range(3):
                    try:
                        text = ws.receive_text()
                    except WebSocketDisconnect:
                        raise
                    parsed = json.loads(text)
                    if parsed.get("error") == "lsp_oom":
                        observed_oom = True
                        assert parsed["language"] == "python"
                        # Drain the close.
                        ws.receive_text()
                        break

        assert observed_oom, "expected an lsp_oom error frame before the close"
        assert exc_info.value.code == 4503


def test_non_oom_crash_still_closes_with_lsp_crashed(monkeypatch) -> None:
    """Negative control — EOF with a non-OOM exit code keeps the legacy ``lsp_crashed``."""
    from app.main import create_app
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()
    _disable_route_gates(monkeypatch)

    class _CrashedLSP(_OOMLSP):
        @property
        def exit_code(self) -> int | None:
            # 1 = ordinary failure, not an OOM signature.
            return 1 if self._yielded_eof else None

    class _CrashedDriver(_FakeDriver):
        async def spawn_lsp(self, _handle, language: str):
            return _CrashedLSP(language)

    pool = _FakePool(
        SandboxHandle(
            id="handle-crash",
            driver="local",
            workdir=Path("/tmp/lsp-crash-test"),
            mission_id="mission-test",
            session_id=uuid.uuid4(),
        )
    )
    pool.driver = _CrashedDriver()  # type: ignore[assignment]

    app = create_app()

    with TestClient(app) as client:
        app.state.sandbox_pool = pool
        url = (
            f"/ws/sessions/{pool._handle.session_id}/lsp"
            f"?language=python&token=anything"
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                url, subprotocols=["lsp.openagentdojo.v1"]
            ) as ws:
                for _ in range(3):
                    try:
                        ws.receive_text()
                    except WebSocketDisconnect:
                        raise

        # The non-OOM crash uses 1011, NOT 4503.
        assert exc_info.value.code == 1011
