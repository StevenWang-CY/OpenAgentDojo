"""Short-lived HMAC tokens for WebSocket auth.

We avoid sending the long-lived session cookie over WS upgrade query params.
Instead, the REST layer issues a 60-second HMAC token bound to a session id.

For reconnect flows we also expose ``refresh_ws_token`` — it validates an
existing token (with a small grace window to absorb clock skew) and reissues
a new short-lived token bound to the same session id. On WS close with code
4401 the frontend will mint a fresh token via the REST API and reconnect.
"""

from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256

from app.config import get_settings

_TOKEN_TTL_S = 60

# Grace window applied to *refresh* only (never to first-issue verification).
# This absorbs clock drift between API replicas without weakening the security
# bound: an expired token can only ever be exchanged for a *new* token bound to
# the same session id; replay still fails ``verify_ws_token`` outside the grace.
_REFRESH_GRACE_S = 60


class WsTokenError(Exception):
    """Raised when a token cannot be refreshed."""


def _sign(payload: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload, sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def _b64encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64decode(payload: str) -> bytes:
    pad = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + pad)


def issue_ws_token(session_id: str, secret: str | None = None) -> str:
    """Return an HMAC-signed token of the form ``<base64-payload>.<base64-mac>``."""
    settings_secret = secret or get_settings().session_secret
    expires_at = int(time.time()) + _TOKEN_TTL_S
    payload = f"{session_id}:{expires_at}".encode()
    return f"{_b64encode(payload)}.{_sign(payload, settings_secret)}"


def verify_ws_token(token: str, session_id: str, secret: str | None = None) -> bool:
    """Return True if ``token`` is a valid, non-expired signature for ``session_id``."""
    if not token or "." not in token:
        return False
    settings_secret = secret or get_settings().session_secret
    try:
        payload_b64, mac_b64 = token.split(".", 1)
        payload = _b64decode(payload_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False

    expected_mac = _sign(payload, settings_secret)
    if not hmac.compare_digest(expected_mac, mac_b64):
        return False

    try:
        sid_in_token, exp_str = payload.decode("utf-8").split(":")
        expires_at = int(exp_str)
    except (ValueError, UnicodeDecodeError):
        return False

    if sid_in_token != session_id:
        return False
    if expires_at < int(time.time()):
        return False
    return True


def _parse_token(token: str, secret: str) -> tuple[str, int]:
    """Return ``(session_id, expires_at)`` if the MAC is valid, else raise."""
    if not token or "." not in token:
        raise WsTokenError("malformed token")
    try:
        payload_b64, mac_b64 = token.split(".", 1)
        payload = _b64decode(payload_b64)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise WsTokenError("malformed token") from exc

    expected_mac = _sign(payload, secret)
    if not hmac.compare_digest(expected_mac, mac_b64):
        raise WsTokenError("bad signature")

    try:
        sid_in_token, exp_str = payload.decode("utf-8").split(":")
        expires_at = int(exp_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WsTokenError("malformed payload") from exc

    return sid_in_token, expires_at


def refresh_ws_token(
    old_token: str,
    session_id: str,
    db: object | None = None,  # accepted for symmetry with REST handlers
    *,
    secret: str | None = None,
    grace_seconds: int = _REFRESH_GRACE_S,
) -> str:
    """Validate ``old_token`` and reissue a fresh token for ``session_id``.

    ``old_token`` may be up to ``grace_seconds`` past its ``expires_at`` — this
    is the clock-skew window. The signature itself MUST still verify, and the
    session id baked into the token MUST match the requested session id.

    Raises :class:`WsTokenError` on any of: bad signature, mismatched session
    id, or token expired beyond the grace window. The ``db`` parameter is
    accepted (but currently unused) so callers in the REST layer can keep a
    single dependency-injection shape.
    """
    settings_secret = secret or get_settings().session_secret
    sid_in_token, expires_at = _parse_token(old_token, settings_secret)

    if sid_in_token != session_id:
        raise WsTokenError("session id mismatch")

    now = int(time.time())
    if expires_at + grace_seconds < now:
        raise WsTokenError("token expired beyond grace window")

    return issue_ws_token(session_id, secret=settings_secret)
