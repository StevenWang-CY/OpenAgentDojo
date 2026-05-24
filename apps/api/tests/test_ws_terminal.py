"""WebSocket auth tokens accept valid signatures and reject bad ones.

The v1 payload bakes ``user_id`` and ``epoch`` into the signed token (see
P0-2). ``verify_ws_token`` looks up the user's current ``session_epoch``
to refuse stale claims; these tests stub that lookup so they exercise the
HMAC + payload layer in isolation. The full DB-backed epoch path is
covered in ``test_ws_epoch_invalidation.py``.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.ws import auth as ws_auth
from app.ws.auth import (
    WsTokenError,
    clear_epoch_cache,
    issue_ws_token,
    refresh_ws_token,
    verify_ws_token,
)

_SECRET = "test-secret-32-chars-min-aaaaaaaa"
_UID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _stub_epoch_lookup(monkeypatch):
    """Pin the user-row lookup so unit tests don't need a DB.

    Returns epoch=1 for any user_id — matches the value issue_ws_token
    bakes into the payload below, so verify_ws_token always sees a
    current claim.
    """
    clear_epoch_cache()
    monkeypatch.setattr(ws_auth, "_load_current_epoch", lambda uid: 1)
    yield
    clear_epoch_cache()


def _issue(sid: str, *, secret: str = _SECRET, epoch: int = 1) -> str:
    return issue_ws_token(sid, user_id=_UID, epoch=epoch, secret=secret)


def test_token_roundtrip() -> None:
    sid = str(uuid.uuid4())
    token = _issue(sid)
    assert verify_ws_token(token, sid, secret=_SECRET) is True


def test_token_rejected_for_wrong_session() -> None:
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    token = _issue(sid_a)
    assert verify_ws_token(token, sid_b, secret=_SECRET) is False


def test_token_rejected_for_bad_signature() -> None:
    sid = str(uuid.uuid4())
    token = _issue(sid)
    tampered = token[:-2] + "xx"
    assert verify_ws_token(tampered, sid, secret=_SECRET) is False


def test_token_rejected_when_expired(monkeypatch) -> None:
    sid = str(uuid.uuid4())
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    token = _issue(sid)
    # Jump time past the TTL.
    monkeypatch.setattr(time, "time", lambda: 1000.0 + 3600)
    assert verify_ws_token(token, sid, secret=_SECRET) is False


# ---------------------------------------------------------------------------
# refresh_ws_token
# ---------------------------------------------------------------------------


def test_refresh_within_grace_returns_fresh_valid_token(monkeypatch) -> None:
    sid = str(uuid.uuid4())
    monkeypatch.setattr(time, "time", lambda: 2_000.0)
    old = _issue(sid)

    # 30s past expiry but well inside the 60s grace window.
    monkeypatch.setattr(time, "time", lambda: 2_000.0 + 60 + 30)

    new = refresh_ws_token(old, sid, secret=_SECRET)
    assert new != old
    # Reset clock so we can verify the freshly-issued token.
    monkeypatch.setattr(time, "time", lambda: 2_000.0 + 60 + 30 + 1)
    assert verify_ws_token(new, sid, secret=_SECRET) is True


def test_refresh_rejects_mismatched_session(monkeypatch) -> None:
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    old = _issue(sid_a)
    with pytest.raises(WsTokenError):
        refresh_ws_token(old, sid_b, secret=_SECRET)


def test_refresh_rejects_token_past_grace(monkeypatch) -> None:
    sid = str(uuid.uuid4())
    monkeypatch.setattr(time, "time", lambda: 3_000.0)
    old = _issue(sid)
    # 60s TTL + 60s grace = 120s headroom. Push past it.
    monkeypatch.setattr(time, "time", lambda: 3_000.0 + 60 + 60 + 5)
    with pytest.raises(WsTokenError):
        refresh_ws_token(old, sid, secret=_SECRET)


def test_refresh_rejects_bad_signature() -> None:
    sid = str(uuid.uuid4())
    old = _issue(sid)
    tampered = old[:-2] + "zz"
    with pytest.raises(WsTokenError):
        refresh_ws_token(tampered, sid, secret=_SECRET)
