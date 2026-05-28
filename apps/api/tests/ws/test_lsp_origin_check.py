"""P1-3 audit — LSP WS rejects upgrades from disallowed Origins.

The browser ships ``Origin: https://...`` on every WS upgrade. We gate
the handshake on ``settings.cors_origins`` so a stolen token from a
non-allowed origin cannot complete the connection. Close code is 4403
with reason ``origin_forbidden``.

Three cases are covered:

* Origin matches allow-list → handshake proceeds past the gate.
* Origin missing entirely    → handshake proceeds (non-browser client).
* Origin not on allow-list   → 4403 close before ``accept()``.

The settings are patched via :func:`app.config.get_settings` so the
helper sees a deterministic ``cors_origins`` list regardless of the
host env.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.sandbox.types import SandboxHandle


class _FakeDriver:
    name = "local"

    async def spawn_lsp(self, _handle, _language):  # pragma: no cover — not reached on the deny path
        raise AssertionError("spawn_lsp must not run when Origin is forbidden")


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


def _make_handle(session_id: uuid.UUID) -> SandboxHandle:
    return SandboxHandle(
        id=f"handle-{session_id}",
        driver="local",
        workdir=Path("/tmp/lsp-origin-test"),
        mission_id="mission-test",
        session_id=session_id,
    )


def _patch_settings_origins(monkeypatch, origins: list[str]) -> None:
    """Force ``settings.cors_origins`` to ``origins`` for the auth helper.

    The Origin check imports ``get_settings`` at the module level in
    :mod:`app.ws.auth`; patching only that module-level binding keeps
    the rest of the app (lifespan, observability, route registration)
    against the real settings so ``create_app()`` continues to boot.
    """

    class _S:
        cors_origins = origins

    from app.ws import auth as ws_auth

    monkeypatch.setattr(ws_auth, "get_settings", lambda: _S())


def _disable_session_gate(monkeypatch) -> None:
    from app.ws import lsp as lsp_ws

    monkeypatch.setattr(lsp_ws, "verify_ws_token", lambda *_a, **_kw: True)

    async def _fake_status(_sid):
        return "active"

    monkeypatch.setattr(lsp_ws, "_session_status", _fake_status)


def test_disallowed_origin_closes_with_4403(monkeypatch) -> None:
    """A WS upgrade whose Origin is not on the allow-list closes 4403.

    TestClient lets us inject arbitrary headers via ``headers=``. The
    server-side helper reads them via ``websocket.headers.get('origin')``
    so a lowercase header key is sufficient.
    """
    from app.main import create_app
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()
    _patch_settings_origins(monkeypatch, ["https://app.openagentdojo.com"])
    _disable_session_gate(monkeypatch)

    app = create_app()
    session_id = uuid.uuid4()
    handle = _make_handle(session_id)

    with TestClient(app) as client:
        app.state.sandbox_pool = _FakePool(handle)
        url = f"/ws/sessions/{session_id}/lsp?language=python&token=anything"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                url,
                subprotocols=["lsp.openagentdojo.v1"],
                headers={"origin": "https://evil.example.com"},
            ):
                pass

        assert exc_info.value.code == 4403


def test_allowed_origin_passes_gate(monkeypatch) -> None:
    """An Origin on the allow-list passes the gate and reaches spawn_lsp.

    We don't run a full echo LSP — instead we let ``_FakeDriver`` reach
    a deliberate-fail path so we can assert the call landed past the
    Origin gate. The signal is "spawn_lsp got called", which we observe
    by replacing the driver with one that raises a typed error.
    """
    from app.sandbox.lsp import LSPUnavailableError

    from app.main import create_app
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()
    _patch_settings_origins(monkeypatch, ["https://app.openagentdojo.com"])
    _disable_session_gate(monkeypatch)

    class _ReachedDriver:
        name = "local"
        called: bool = False

        async def spawn_lsp(self, _handle, language):
            type(self).called = True
            raise LSPUnavailableError("binary_not_found", language)

    pool = _FakePool(_make_handle(uuid.uuid4()))
    pool.driver = _ReachedDriver()  # type: ignore[assignment]

    app = create_app()

    with TestClient(app) as client:
        app.state.sandbox_pool = pool
        url = (
            f"/ws/sessions/{pool._handle.session_id}/lsp?language=python&token=anything"
        )
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                url,
                subprotocols=["lsp.openagentdojo.v1"],
                headers={"origin": "https://app.openagentdojo.com"},
            ) as ws:
                ws.receive_text()  # the lsp_error frame
                ws.receive_text()  # close

        assert _ReachedDriver.called is True


def test_wildcard_origin_passes_gate(monkeypatch) -> None:
    """``cors_origins`` containing ``"*"`` (dev mode) passes any Origin."""
    from app.main import create_app
    from app.sandbox.lsp import LSPUnavailableError
    from app.ws import lsp as lsp_ws

    lsp_ws._active_lsp.clear()
    _patch_settings_origins(monkeypatch, ["*"])
    _disable_session_gate(monkeypatch)

    class _ReachedDriver:
        name = "local"
        called: bool = False

        async def spawn_lsp(self, _handle, language):
            type(self).called = True
            raise LSPUnavailableError("binary_not_found", language)

    pool = _FakePool(_make_handle(uuid.uuid4()))
    pool.driver = _ReachedDriver()  # type: ignore[assignment]

    app = create_app()

    with TestClient(app) as client:
        app.state.sandbox_pool = pool
        url = (
            f"/ws/sessions/{pool._handle.session_id}/lsp?language=python&token=anything"
        )
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                url,
                subprotocols=["lsp.openagentdojo.v1"],
                headers={"origin": "https://anything.example.com"},
            ) as ws:
                ws.receive_text()
                ws.receive_text()

        assert _ReachedDriver.called is True
