"""P1-3 audit — LSP WS proxy refuses to attach while apply-patch is in flight.

Scenario: an apply-patch is mid-execution against the sandbox handle. The
``_ActivityTrackedDriver`` wrapper bumps ``apply_diff_busy_count`` in the
handle's driver_state for the duration of the call; ``SandboxPool.is_busy``
reads that counter. The LSP WS proxy gates the upgrade on the busy check
and MUST close with code 4503 + reason ``sandbox_busy`` (and emit a
matching :class:`LSPErrorFrame`) when the sandbox is busy.

This test stubs out the busy state directly via the handle's
``driver_state`` so we don't need a real apply-patch to be in flight —
the only invariant the proxy depends on is the counter value, and that's
what we drive.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.sandbox.types import SandboxHandle


class _FakeDriver:
    """Driver stub — never actually spawns an LSP because the busy gate fires first."""

    name = "local"

    async def spawn_lsp(self, _handle, _language):  # pragma: no cover — not reached
        raise AssertionError(
            "spawn_lsp must not be called when the sandbox is busy"
        )


class _BusyPool:
    """Fake pool that reports busy=True for the configured handle.

    Mirrors the production :class:`app.sandbox.pool.SandboxPool` surface
    the WS proxy actually touches: ``driver``, ``handle_for``,
    ``handles_snapshot``, ``is_busy``.
    """

    def __init__(self, handle: SandboxHandle) -> None:
        self._handle = handle
        self.driver = _FakeDriver()

    def handle_for(self, session_id: uuid.UUID) -> SandboxHandle | None:
        return self._handle if session_id == self._handle.session_id else None

    def handles_snapshot(self) -> list[SandboxHandle]:
        return [self._handle]

    def is_busy(self, handle: SandboxHandle) -> bool:
        # Production reads ``driver_state["apply_diff_busy_count"]``; mirror
        # the same path here so the test exercises the real predicate
        # rather than a one-off boolean.
        return (
            int(handle.driver_state.get("apply_diff_busy_count") or 0) > 0
        )


def _make_handle(session_id: uuid.UUID) -> SandboxHandle:
    return SandboxHandle(
        id=f"handle-{session_id}",
        driver="local",
        workdir=Path("/tmp/lsp-busy-test"),
        mission_id="mission-test",
        session_id=session_id,
        driver_state={"apply_diff_busy_count": 1},  # already mid-patch
    )


def _disable_route_gates(monkeypatch) -> None:
    from app.ws import lsp as lsp_ws

    monkeypatch.setattr(lsp_ws, "verify_ws_token", lambda *_a, **_kw: True)
    # Skip the Origin check — tested separately in test_lsp_origin_check.
    monkeypatch.setattr(lsp_ws, "is_allowed_origin", lambda *_a, **_kw: True)

    async def _fake_status(_sid):
        return "active"

    monkeypatch.setattr(lsp_ws, "_session_status", _fake_status)


def test_busy_sandbox_refuses_lsp_attach(monkeypatch) -> None:
    """4503 sandbox_busy when apply-patch is in flight.

    The WS proxy closes BEFORE spawning the LSP so the patch can finish
    rewriting the working copy without an LSP indexing a half-applied
    tree underneath it. The FE retries with backoff once it sees 4503.
    """
    from app.main import create_app
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()

    app = create_app()
    _disable_route_gates(monkeypatch)

    session_id = uuid.uuid4()
    handle = _make_handle(session_id)

    with TestClient(app) as client:
        app.state.sandbox_pool = _BusyPool(handle)
        url = f"/ws/sessions/{session_id}/lsp?language=python&token=anything"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                url, subprotocols=["lsp.openagentdojo.v1"]
            ) as ws:
                # Frame is sent before the close; read it then expect the
                # close on the next receive.
                text = ws.receive_text()
                parsed = json.loads(text)
                assert parsed["type"] == "lsp_error"
                assert parsed["error"] == "sandbox_busy"
                assert parsed["language"] == "python"
                ws.receive_text()  # raises with the close code

        assert exc_info.value.code == 4503


def test_idle_sandbox_passes_busy_gate(monkeypatch) -> None:
    """Negative control — busy_count=0 attaches normally.

    Verifies the busy gate is not stuck-on: a handle whose counter is
    zero proceeds to spawn_lsp (which we error out cleanly so we don't
    have to plumb a full echo LSP).
    """
    from app.sandbox.lsp import LSPUnavailableError

    from app.main import create_app
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()

    app = create_app()
    _disable_route_gates(monkeypatch)

    session_id = uuid.uuid4()
    handle = SandboxHandle(
        id=f"handle-{session_id}",
        driver="local",
        workdir=Path("/tmp/lsp-busy-test-2"),
        mission_id="mission-test",
        session_id=session_id,
        driver_state={"apply_diff_busy_count": 0},  # idle
    )

    class _NotBusyPool(_BusyPool):
        def is_busy(self, _handle: SandboxHandle) -> bool:
            return False

    class _SpawnFailDriver:
        name = "local"

        async def spawn_lsp(self, _handle, language):
            raise LSPUnavailableError("binary_not_found", language)

    pool = _NotBusyPool(handle)
    pool.driver = _SpawnFailDriver()  # type: ignore[assignment]

    with TestClient(app) as client:
        app.state.sandbox_pool = pool
        url = f"/ws/sessions/{session_id}/lsp?language=python&token=anything"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                url, subprotocols=["lsp.openagentdojo.v1"]
            ) as ws:
                text = ws.receive_text()
                parsed = json.loads(text)
                # Past the busy gate; we hit the spawn_lsp failure path,
                # NOT the sandbox_busy path. That's the assertion.
                assert parsed["error"] != "sandbox_busy"
                assert parsed["error"] == "binary_not_found"
                ws.receive_text()

        assert exc_info.value.code == 4503
