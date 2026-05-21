"""WebSocket auth tokens accept valid signatures and reject bad ones."""

from __future__ import annotations

import time
import uuid

import pytest

from app.ws.auth import WsTokenError, issue_ws_token, refresh_ws_token, verify_ws_token

_SECRET = "test-secret-32-chars-min-aaaaaaaa"


def test_token_roundtrip() -> None:
    sid = str(uuid.uuid4())
    token = issue_ws_token(sid, secret="test-secret-32-chars-min-aaaaaaaa")
    assert verify_ws_token(token, sid, secret="test-secret-32-chars-min-aaaaaaaa") is True


def test_token_rejected_for_wrong_session() -> None:
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    token = issue_ws_token(sid_a, secret="test-secret-32-chars-min-aaaaaaaa")
    assert verify_ws_token(token, sid_b, secret="test-secret-32-chars-min-aaaaaaaa") is False


def test_token_rejected_for_bad_signature() -> None:
    sid = str(uuid.uuid4())
    token = issue_ws_token(sid, secret="test-secret-32-chars-min-aaaaaaaa")
    tampered = token[:-2] + "xx"
    assert verify_ws_token(tampered, sid, secret="test-secret-32-chars-min-aaaaaaaa") is False


def test_token_rejected_when_expired(monkeypatch) -> None:
    sid = str(uuid.uuid4())
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    token = issue_ws_token(sid, secret="test-secret-32-chars-min-aaaaaaaa")
    # Jump time past the TTL.
    monkeypatch.setattr(time, "time", lambda: 1000.0 + 3600)
    assert verify_ws_token(token, sid, secret="test-secret-32-chars-min-aaaaaaaa") is False


# ---------------------------------------------------------------------------
# refresh_ws_token
# ---------------------------------------------------------------------------


def test_refresh_within_grace_returns_fresh_valid_token(monkeypatch) -> None:
    sid = str(uuid.uuid4())
    monkeypatch.setattr(time, "time", lambda: 2_000.0)
    old = issue_ws_token(sid, secret=_SECRET)

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
    old = issue_ws_token(sid_a, secret=_SECRET)
    with pytest.raises(WsTokenError):
        refresh_ws_token(old, sid_b, secret=_SECRET)


def test_refresh_rejects_token_past_grace(monkeypatch) -> None:
    sid = str(uuid.uuid4())
    monkeypatch.setattr(time, "time", lambda: 3_000.0)
    old = issue_ws_token(sid, secret=_SECRET)
    # 60s TTL + 60s grace = 120s headroom. Push past it.
    monkeypatch.setattr(time, "time", lambda: 3_000.0 + 60 + 60 + 5)
    with pytest.raises(WsTokenError):
        refresh_ws_token(old, sid, secret=_SECRET)


def test_refresh_rejects_bad_signature() -> None:
    sid = str(uuid.uuid4())
    old = issue_ws_token(sid, secret=_SECRET)
    tampered = old[:-2] + "zz"
    with pytest.raises(WsTokenError):
        refresh_ws_token(tampered, sid, secret=_SECRET)
