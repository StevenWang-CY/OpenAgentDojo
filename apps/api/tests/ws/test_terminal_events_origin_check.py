"""P0 audit — terminal & events WS reject upgrades from disallowed Origins.

The browser ships ``Origin: https://...`` on every WS upgrade. Only the
LSP endpoint gated it before this fix; ``terminal_ws`` and ``events_ws``
accepted any cross-origin upgrade that carried a valid token because
``CORSMiddleware`` does NOT cover the WS handshake. We now mirror
:mod:`app.ws.lsp` exactly: gate on ``settings.cors_origins`` and close
4403 / ``origin_forbidden`` before ``accept()``.

Covered for BOTH terminal and events:

* Origin not on allow-list → 4403 close before ``accept()`` (the bug).
* Origin on the allow-list  → handshake proceeds past the Origin gate.

The settings are patched via :func:`app.config.get_settings` (the binding
the auth helper imports at module scope) so the helper sees a
deterministic ``cors_origins`` list regardless of the host env.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _patch_settings_origins(monkeypatch, origins: list[str]) -> None:
    """Force ``settings.cors_origins`` to ``origins`` for the auth helper.

    The Origin check imports ``get_settings`` at the module level in
    :mod:`app.ws.auth`; patching only that module-level binding keeps the
    rest of the app (lifespan, observability, route registration) against
    the real settings so ``create_app()`` continues to boot.
    """

    class _S:
        cors_origins = origins

    from app.ws import auth as ws_auth

    monkeypatch.setattr(ws_auth, "get_settings", lambda: _S())


def _disable_token_and_session_gates(monkeypatch, module) -> None:
    """Make the token + session-existence gates pass so the Origin gate is the
    only thing that can close the socket.

    ``verify_ws_token`` is patched on the endpoint module (the name the
    handler still calls on its fast path); ``_session_exists`` is patched
    so the upgrade doesn't need a DB row.
    """
    monkeypatch.setattr(module, "verify_ws_token", lambda *_a, **_kw: True)

    async def _fake_exists(_sid):
        return True

    monkeypatch.setattr(module, "_session_exists", _fake_exists)


@pytest.mark.parametrize("endpoint", ["terminal", "events"])
def test_disallowed_origin_closes_with_4403(monkeypatch, endpoint) -> None:
    """A valid-token upgrade whose Origin is off the allow-list closes 4403.

    This is the regression the P0 fix targets: before the fix these two
    endpoints accepted the cross-origin upgrade outright.
    """
    from app.main import create_app

    if endpoint == "terminal":
        from app.ws import terminal as mod
    else:
        from app.ws import events as mod

    _patch_settings_origins(monkeypatch, ["https://app.openagentdojo.com"])
    _disable_token_and_session_gates(monkeypatch, mod)

    app = create_app()
    session_id = uuid.uuid4()

    with TestClient(app) as client:
        # No sandbox pool needed — the Origin gate fires before any pool lookup.
        url = f"/ws/sessions/{session_id}/{endpoint}?token=anything"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                url,
                headers={"origin": "https://evil.example.com"},
            ):
                pass

        assert exc_info.value.code == 4403


@pytest.mark.parametrize("endpoint", ["terminal", "events"])
def test_allowed_origin_passes_origin_gate(monkeypatch, endpoint) -> None:
    """An Origin on the allow-list passes the gate (no 4403 close).

    The handler accepts the upgrade and then closes for an unrelated,
    deterministic reason — crucially NOT 4403. The signal under test is
    "the Origin gate did not fire", which we assert by checking the close
    code is something other than ``origin_forbidden``.

    The two endpoints reach a terminating close differently once past the
    gate, so we drive each to a deterministic one:

    * ``terminal`` — with no sandbox pool attached it emits a ``no_sandbox``
      error frame and closes 1011 (internal error).
    * ``events`` — it has no no-pool early-out (the stream is DB+Redis
      backed, not sandbox backed); instead we make the backfill return a
      single ``submission.graded`` event so the handler closes with the
      graded normal-closure code in one round trip rather than parking in
      the live-subscription / poll loop.
    """
    from app.main import create_app

    if endpoint == "terminal":
        from app.ws import terminal as mod
    else:
        from app.ws import events as mod

    _patch_settings_origins(monkeypatch, ["https://app.openagentdojo.com"])
    _disable_token_and_session_gates(monkeypatch, mod)

    if endpoint == "events":
        # Force a graded backfill so the handler terminates deterministically
        # right after ``accept()`` instead of blocking in the poll/subscribe
        # loop (which never self-closes without a graded event).
        async def _graded_backfill(_session_id, _last_id):
            return [
                {
                    "id": 1,
                    "session_id": str(_session_id),
                    "event_type": "submission.graded",
                    "payload": {},
                    "occurred_at": "2026-01-01T00:00:00.000000Z",
                }
            ]

        monkeypatch.setattr(mod, "_backfill", _graded_backfill)

    app = create_app()
    session_id = uuid.uuid4()

    with TestClient(app) as client:
        # No sandbox pool attached. For terminal this drives the no-sandbox
        # close; for events the pool is irrelevant. The point is only that the
        # Origin gate let the upgrade through.
        app.state.sandbox_pool = None
        url = f"/ws/sessions/{session_id}/{endpoint}?token=anything"
        try:
            with client.websocket_connect(
                url,
                headers={"origin": "https://app.openagentdojo.com"},
            ) as ws:
                # Drain whatever the handler emits before closing; we only
                # assert the close code below, so swallow frames/errors.
                try:
                    ws.receive()
                except Exception:
                    pass
        except WebSocketDisconnect as exc:
            assert exc.code != 4403
