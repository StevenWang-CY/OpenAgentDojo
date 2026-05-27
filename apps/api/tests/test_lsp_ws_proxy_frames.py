"""P1-3 — the WS proxy pumps bytes byte-faithfully in both directions.

We replace the driver's ``spawn_lsp`` with a fake that echoes stdin → stdout
through an in-memory pair of asyncio queues. Connecting Starlette's
TestClient WS to the route, we then assert the bytes we sent land
back unmodified — which is the only guarantee the language-client side
actually depends on (LSP framing is its concern, not ours).
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.sandbox.lsp import LSPProcess
from app.sandbox.types import SandboxHandle

# ---------------------------------------------------------------------------
# In-memory echo LSP — the simplest thing that exercises the byte pump.
# ---------------------------------------------------------------------------


class _EchoLSP(LSPProcess):
    """Pumps stdin chunks straight back out via stdout. No framing assumed."""

    def __init__(self) -> None:
        super().__init__("python")
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._alive = True

    @property
    def alive(self) -> bool:
        return self._alive

    async def write_stdin(self, data: bytes) -> None:
        if data:
            await self._queue.put(data)

    async def read_stdout(self) -> bytes:
        # Block until either bytes arrive OR we get shut down. The proxy's
        # ``b""`` sentinel handles "EOF" so we surface that on shutdown.
        if not self._alive:
            return b""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=5.0)
        except TimeoutError:
            return b""

    async def shutdown(self, *, timeout_s: float = 2.0) -> None:
        self._alive = False
        # Unblock any waiting read_stdout so the pump task can exit.
        try:
            self._queue.put_nowait(b"")
        except asyncio.QueueFull:  # pragma: no cover — unbounded queue
            pass


# ---------------------------------------------------------------------------
# Test infra — drop the routes' guards down to deterministic stubs.
# ---------------------------------------------------------------------------


class _FakeDriver:
    name = "local"

    def __init__(self, lsp_factory) -> None:
        self._lsp_factory = lsp_factory

    async def spawn_lsp(self, handle, language: str):
        return await self._lsp_factory(handle, language)


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
        workdir=Path("/tmp/lsp-proxy-test"),
        mission_id="mission-test",
        session_id=session_id,
    )


def _disable_route_gates(monkeypatch) -> None:
    """Bypass auth + session-status checks so we can focus on the pump."""
    from app.ws import lsp as lsp_ws

    monkeypatch.setattr(lsp_ws, "verify_ws_token", lambda *_args, **_kwargs: True)

    async def _fake_status(_sid):
        return "active"

    monkeypatch.setattr(lsp_ws, "_session_status", _fake_status)


# ---------------------------------------------------------------------------
# The actual test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_proxy_echoes_bytes_unmodified(monkeypatch) -> None:
    """16 random bytes sent over the WS must arrive back identically.

    Validates two invariants at once:
      * Binary frames received from the FE land on the LSP stdin unchanged.
      * Whatever the LSP writes to stdout lands back on the FE as a binary
        frame, again unchanged.
    """
    # Fresh app per test so we don't share the in-process LSP registry
    # across cases.
    from app.main import create_app
    from app.ws import lsp as lsp_ws

    # Reset any leaked state from prior tests.
    lsp_ws._active_lsp.clear()

    app = create_app()
    _disable_route_gates(monkeypatch)

    session_id = uuid.uuid4()
    handle = _make_handle(session_id)
    echo_lsp = _EchoLSP()

    async def _factory(_handle, _language):
        return echo_lsp

    with TestClient(app) as client:
        # The lifespan installs a real SandboxPool on app.state; we override
        # it AFTER startup so our fake survives. The WS endpoint reads
        # ``websocket.app.state.sandbox_pool`` per-request, so reassigning
        # after startup takes effect immediately.
        app.state.sandbox_pool = _FakePool(handle, _FakeDriver(_factory))

        payload = secrets.token_bytes(16)
        url = f"/ws/sessions/{session_id}/lsp?language=python&token=anything"
        with client.websocket_connect(
            url, subprotocols=["lsp.openagentdojo.v1"]
        ) as ws:
            ws.send_bytes(payload)
            received = ws.receive_bytes()
            assert received == payload, (
                f"byte mismatch — sent {payload!r}, received {received!r}"
            )


@pytest.mark.asyncio
async def test_ws_proxy_emits_structured_lsp_error_on_binary_missing(monkeypatch) -> None:
    """LSPUnavailableError must turn into an lsp_error text frame, not 500."""
    import json

    from app.main import create_app
    from app.sandbox.lsp import LSPUnavailableError
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()
    app = create_app()
    _disable_route_gates(monkeypatch)

    session_id = uuid.uuid4()
    handle = _make_handle(session_id)

    async def _broken_factory(_handle, language):
        raise LSPUnavailableError("binary_not_found", language, detail="no pyright")

    with TestClient(app) as client:
        app.state.sandbox_pool = _FakePool(handle, _FakeDriver(_broken_factory))

        url = f"/ws/sessions/{session_id}/lsp?language=python&token=anything"
        with client.websocket_connect(
            url, subprotocols=["lsp.openagentdojo.v1"]
        ) as ws:
            text = ws.receive_text()
            parsed = json.loads(text)
            assert parsed == {
                "type": "lsp_error",
                "error": "binary_not_found",
                "language": "python",
                "detail": "no pyright",
            }
