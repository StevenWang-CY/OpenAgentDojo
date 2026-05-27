"""P1-3 — only one LSP per (session, language) may be attached at a time.

A second concurrent WS upgrade for the same pair must be closed with code
4409 and reason ``lsp_already_running``. The FE's monaco-languageclient
handles the rejection by NOT re-issuing a retry storm (it surfaces the
error in the editor footer until the user navigates away).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.sandbox.lsp import LSPProcess
from app.sandbox.types import SandboxHandle


class _BlockingLSP(LSPProcess):
    """Stays attached indefinitely so the second WS lands while we hold the slot."""

    def __init__(self, language: str = "python") -> None:
        super().__init__(language)
        self._alive = True
        self._stop = asyncio.Event()

    @property
    def alive(self) -> bool:
        return self._alive

    async def write_stdin(self, data: bytes) -> None:
        return

    async def read_stdout(self) -> bytes:
        # Block until the proxy calls shutdown() — long enough for the second
        # WS to attempt to attach and be rejected.
        await self._stop.wait()
        return b""

    async def shutdown(self, *, timeout_s: float = 2.0) -> None:
        self._alive = False
        self._stop.set()


class _FakeDriver:
    name = "local"

    def __init__(self) -> None:
        self.spawned: list[_BlockingLSP] = []

    async def spawn_lsp(self, _handle, language: str) -> _BlockingLSP:
        lsp = _BlockingLSP(language)
        self.spawned.append(lsp)
        return lsp


class _FakePool:
    def __init__(self, handle: SandboxHandle, driver: _FakeDriver) -> None:
        self._handle = handle
        self.driver = driver

    def handle_for(self, session_id: uuid.UUID) -> SandboxHandle | None:
        return self._handle if session_id == self._handle.session_id else None

    def handles_snapshot(self) -> list[SandboxHandle]:
        return [self._handle]


def _make_handle(session_id: uuid.UUID) -> SandboxHandle:
    return SandboxHandle(
        id=f"handle-{session_id}",
        driver="local",
        workdir=Path("/tmp/lsp-dedup-test"),
        mission_id="mission-test",
        session_id=session_id,
    )


def _disable_route_gates(monkeypatch) -> None:
    from app.ws import lsp as lsp_ws

    monkeypatch.setattr(lsp_ws, "verify_ws_token", lambda *_args, **_kwargs: True)

    async def _fake_status(_sid):
        return "active"

    monkeypatch.setattr(lsp_ws, "_session_status", _fake_status)


@pytest.mark.asyncio
async def test_second_lsp_for_same_session_language_is_rejected(monkeypatch) -> None:
    """First WS attaches; second WS for same pair receives lsp_already_running.

    Starlette's TestClient surfaces a closed WS as a
    :class:`starlette.websockets.WebSocketDisconnect` carrying the application
    close code (4409 in our contract). The lsp_error JSON text frame is sent
    BEFORE the close, so we read it first.
    """
    from starlette.websockets import WebSocketDisconnect

    from app.main import create_app
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()

    app = create_app()
    _disable_route_gates(monkeypatch)

    session_id = uuid.uuid4()
    handle = _make_handle(session_id)
    driver = _FakeDriver()

    with TestClient(app) as client:
        app.state.sandbox_pool = _FakePool(handle, driver)
        url = f"/ws/sessions/{session_id}/lsp?language=python&token=anything"
        with client.websocket_connect(
            url, subprotocols=["lsp.openagentdojo.v1"]
        ):
            # First connection is up; the proxy's spawn task has installed
            # the LSP in the registry. Try a second concurrent connection.
            try:
                with client.websocket_connect(
                    url, subprotocols=["lsp.openagentdojo.v1"]
                ) as second:
                    text = second.receive_text()
                    parsed = json.loads(text)
                    assert parsed["type"] == "lsp_error"
                    assert parsed["error"] == "lsp_already_running"
                    assert parsed["language"] == "python"
                    # Reading again must raise the disconnect with code 4409.
                    with pytest.raises(WebSocketDisconnect) as exc_info:
                        second.receive_text()
                    assert exc_info.value.code == 4409
            except WebSocketDisconnect as exc:
                # Some Starlette versions raise on connect when the server
                # closes mid-handshake; either path is acceptable as long
                # as the close code is 4409.
                assert exc.code == 4409
