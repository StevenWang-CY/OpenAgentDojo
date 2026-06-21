"""Unit contract for :func:`app.ws.auth.classify_ws_token` (P1).

The three WS endpoints map the verdict to close codes (EXPIRED→4401,
INVALID→1008, VALID→proceed). This pins the classifier in isolation —
the HMAC + epoch lookup are exercised here without spinning up a route.

The epoch row lookup is stubbed (epoch=1) so a freshly-minted token's
claim is current; the full DB-backed epoch path lives in
``tests/test_ws_epoch_invalidation.py``.
"""

from __future__ import annotations

import uuid

import pytest

from app.ws import auth as ws_auth
from app.ws.auth import (
    WsTokenStatus,
    classify_ws_token,
    clear_epoch_cache,
    issue_ws_token,
    verify_ws_token,
)

_SECRET = "test-secret-32-chars-min-aaaaaaaa"
_UID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _stub_epoch_lookup(monkeypatch):
    clear_epoch_cache()
    monkeypatch.setattr(ws_auth, "_load_current_epoch", lambda _uid: 1)
    yield
    clear_epoch_cache()


def _issue(sid: str, *, epoch: int = 1) -> str:
    return issue_ws_token(sid, user_id=_UID, epoch=epoch, secret=_SECRET)


def test_valid_token_classifies_valid() -> None:
    sid = str(uuid.uuid4())
    token = _issue(sid)
    assert classify_ws_token(token, sid, secret=_SECRET) is WsTokenStatus.VALID


def test_expired_token_classifies_expired(monkeypatch) -> None:
    """A token whose ONLY defect is a lapsed TTL is EXPIRED, not INVALID."""
    sid = str(uuid.uuid4())
    monkeypatch.setattr(ws_auth.time, "time", lambda: 5_000.0)
    token = _issue(sid)
    # Jump past the 60s TTL.
    monkeypatch.setattr(ws_auth.time, "time", lambda: 5_000.0 + 3_600)
    assert classify_ws_token(token, sid, secret=_SECRET) is WsTokenStatus.EXPIRED


def test_bad_signature_classifies_invalid() -> None:
    sid = str(uuid.uuid4())
    tampered = _issue(sid)[:-2] + "xx"
    assert classify_ws_token(tampered, sid, secret=_SECRET) is WsTokenStatus.INVALID


def test_wrong_session_classifies_invalid() -> None:
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    token = _issue(sid_a)
    assert classify_ws_token(token, sid_b, secret=_SECRET) is WsTokenStatus.INVALID


def test_stale_epoch_classifies_invalid(monkeypatch) -> None:
    """A current-signature token whose epoch claim is stale is INVALID, not EXPIRED.

    Signing out everywhere rotates the row epoch; a fresh-but-stale-epoch
    token must be fatal (re-minting would itself fail the epoch check), so
    it maps to 1008 rather than the re-mintable 4401.
    """
    sid = str(uuid.uuid4())
    token = _issue(sid, epoch=1)
    monkeypatch.setattr(ws_auth, "_load_current_epoch", lambda _uid: 2)
    assert classify_ws_token(token, sid, secret=_SECRET) is WsTokenStatus.INVALID


def test_verify_ws_token_is_valid_only(monkeypatch) -> None:
    """The boolean wrapper stays True only for VALID and False for the rest."""
    sid = str(uuid.uuid4())
    assert verify_ws_token(_issue(sid), sid, secret=_SECRET) is True

    monkeypatch.setattr(ws_auth.time, "time", lambda: 9_000.0)
    expired = _issue(sid)
    monkeypatch.setattr(ws_auth.time, "time", lambda: 9_000.0 + 3_600)
    assert verify_ws_token(expired, sid, secret=_SECRET) is False
