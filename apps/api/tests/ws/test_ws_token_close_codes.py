"""P1 audit — WS token rejection maps EXPIRED→4401 and INVALID→1008.

``ws.ts`` reserves close code 4401 for "token expired → re-mint + reconnect"
and treats 1008 as fatal. Before this fix the backend closed BOTH an expired
and a forged token with 1008, so a 60s WS token lapsing mid-session
permanently killed the stream instead of triggering a silent re-mint.

The fix routes the token verdict through
:func:`app.ws.auth.classify_ws_token`:

* an otherwise-valid-but-EXPIRED token → 4401 / ``token expired``
* a malformed / forged / wrong-session token → 1008 / ``bad token``
* a valid token + allowed Origin → the handshake proceeds past the gate.

All three WS endpoints (terminal, events, lsp) share the contract, so each
case is parametrised across the three routes.

Tokens are minted with an explicit secret and the same secret is pinned on
the patched ``settings`` object the auth helper reads, so ``classify_ws_token``
runs its real HMAC + expiry path end-to-end through the route.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.ws import auth as ws_auth
from app.ws.auth import issue_ws_token

_SECRET = "test-secret-32-chars-min-aaaaaaaa"
_ALLOWED_ORIGIN = "https://app.openagentdojo.com"
_UID = str(uuid.uuid4())


def _url(endpoint: str, session_id: uuid.UUID, token: str) -> str:
    base = f"/ws/sessions/{session_id}/{endpoint}?token={token}"
    if endpoint == "lsp":
        return base + "&language=python"
    return base


def _connect_kwargs(endpoint: str) -> dict:
    kwargs: dict = {"headers": {"origin": _ALLOWED_ORIGIN}}
    if endpoint == "lsp":
        kwargs["subprotocols"] = ["lsp.openagentdojo.v1"]
    return kwargs


@pytest.fixture
def _pin_settings(monkeypatch):
    """Pin ``cors_origins`` + ``session_secret`` on the auth helper.

    The token verifier and the Origin helper both read ``get_settings`` at
    module scope in :mod:`app.ws.auth`; patching that single binding lets
    the real HMAC + expiry path run against ``_SECRET`` while the rest of
    the app boots against real settings.
    """

    class _S:
        cors_origins = [_ALLOWED_ORIGIN]
        session_secret = _SECRET

    monkeypatch.setattr(ws_auth, "get_settings", lambda: _S())
    # A freshly-minted token claims epoch=1; pin the row lookup to match so a
    # non-expired token classifies VALID rather than tripping the epoch gate.
    ws_auth.clear_epoch_cache()
    monkeypatch.setattr(ws_auth, "_load_current_epoch", lambda _uid: 1)
    yield
    ws_auth.clear_epoch_cache()


def _issue(session_id: uuid.UUID, *, epoch: int = 1) -> str:
    return issue_ws_token(str(session_id), user_id=_UID, epoch=epoch, secret=_SECRET)


@pytest.mark.parametrize("endpoint", ["terminal", "events", "lsp"])
def test_expired_token_closes_4401(monkeypatch, _pin_settings, endpoint) -> None:
    """An otherwise-valid token whose TTL lapsed closes 4401 (re-mintable)."""
    from app.main import create_app

    session_id = uuid.uuid4()

    # Mint at t0, then advance the clock past the 60s TTL so the only defect is
    # the lapsed ``expires_at``. ``issue_ws_token`` and ``classify_ws_token``
    # both read ``time.time`` from the auth module, so patch it there.
    base = 1_000_000
    monkeypatch.setattr(ws_auth.time, "time", lambda: float(base))
    token = _issue(session_id)
    monkeypatch.setattr(ws_auth.time, "time", lambda: float(base + 120))

    app = create_app()
    with TestClient(app) as client:
        url = _url(endpoint, session_id, token)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(url, **_connect_kwargs(endpoint)):
                pass
        assert exc_info.value.code == 4401


@pytest.mark.parametrize("endpoint", ["terminal", "events", "lsp"])
def test_invalid_token_closes_1008(monkeypatch, _pin_settings, endpoint) -> None:
    """A forged / malformed token still closes 1008 (fatal), not 4401."""
    from app.main import create_app

    session_id = uuid.uuid4()
    token = _issue(session_id)
    # Corrupt the signature → bad HMAC → INVALID (never EXPIRED).
    forged = token[:-2] + ("aa" if not token.endswith("aa") else "bb")

    app = create_app()
    with TestClient(app) as client:
        url = _url(endpoint, session_id, forged)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(url, **_connect_kwargs(endpoint)):
                pass
        assert exc_info.value.code == 1008


@pytest.mark.parametrize("endpoint", ["terminal", "events", "lsp"])
def test_valid_token_passes_token_gate(monkeypatch, _pin_settings, endpoint) -> None:
    """A valid token + allowed Origin proceeds past the token gate.

    With no sandbox pool attached, each handler proceeds past the Origin +
    token gates and then closes with its own no-sandbox / spawn code. The
    point under test is that the close is NEITHER 4401 (expired) NOR 1008
    (bad token) — i.e. the valid token was accepted.
    """
    from app.main import create_app

    session_id = uuid.uuid4()
    token = _issue(session_id)

    # Patch the session-existence check on the two endpoints that gate on it so
    # the upgrade reaches the (absent) sandbox-pool path rather than 4404/1008.
    if endpoint == "terminal":
        from app.ws import terminal as mod

        async def _exists(_sid):
            return True

        monkeypatch.setattr(mod, "_session_exists", _exists)
    elif endpoint == "events":
        from app.ws import events as mod

        async def _exists(_sid):
            return True

        monkeypatch.setattr(mod, "_session_exists", _exists)

        # Force a graded backfill so the handler closes deterministically right
        # after accept() instead of parking in the live poll/subscribe loop
        # (which never self-closes). Mirrors test_terminal_events_origin_check.
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
    else:
        from app.ws import lsp as mod

        async def _status(_sid):
            return "active"

        monkeypatch.setattr(mod, "_session_status", _status)
        mod._active_lsp.clear()

    app = create_app()
    with TestClient(app) as client:
        app.state.sandbox_pool = None
        url = _url(endpoint, session_id, token)
        try:
            with client.websocket_connect(url, **_connect_kwargs(endpoint)) as ws:
                # Past the gate, each handler closes for its own deterministic
                # reason (terminal/lsp: no-sandbox; events: the graded backfill
                # above). Drain whatever it emits, then assert below the close
                # was NEITHER 4401 (expired) NOR 1008 (bad token) — i.e. the
                # valid token itself was accepted.
                try:
                    ws.receive()
                except Exception:
                    pass
        except WebSocketDisconnect as exc:
            assert exc.code not in (4401, 1008)
