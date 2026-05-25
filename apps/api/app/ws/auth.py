"""Short-lived HMAC tokens for WebSocket auth.

We avoid sending the long-lived session cookie over WS upgrade query
params. Instead, the REST layer issues a 60-second HMAC token bound to a
session id and the owner's ``users.session_epoch`` at issue time.

For reconnect flows we also expose ``refresh_ws_token`` — it validates an
existing token (with a small grace window to absorb clock skew) and
reissues a new short-lived token bound to the same session id AND the
current session_epoch (not the stale one from the incoming token). On WS
close with code 4401 the frontend will mint a fresh token via the REST
API and reconnect.

Epoch enforcement (P0-2)
------------------------
Every token bakes the user's ``session_epoch`` into the signed payload.
``verify_ws_token`` re-loads ``users.session_epoch`` and rejects any
claim that is older than the current row value. Sign-out-everywhere /
email-change / deletion-schedule all rotate the epoch, so an already-
issued WS token can no longer authenticate the terminal after a
rotation — closing the "perpetual fresh token via refresh" loop the
Phase-3 audit caught.

The user-row lookup is cached in-process for 5 seconds so a chatty WS
stream doesn't hammer the DB on every read. The cache is keyed by
``user_id`` and stores only the epoch + insertion time — no PII.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import time
import uuid
from hashlib import sha256

from app.config import get_settings

_TOKEN_TTL_S = 60

# Grace window applied to *refresh* only (never to first-issue verification).
# This absorbs clock drift between API replicas without weakening the security
# bound: an expired token can only ever be exchanged for a *new* token bound to
# the same session id; replay still fails ``verify_ws_token`` outside the grace.
_REFRESH_GRACE_S = 60

# Token payload schema version. Bumped from "v0" (no user/epoch claim — pre-
# P0-2) to "v1" (carries user_id + epoch). The verifier accepts v0 tokens
# only briefly during the rollout window — they fall back to "no epoch
# check" which would defeat the whole point. We therefore REJECT v0 tokens
# outright; every running client mints v1 because every issue site goes
# through this module. The version prefix gives us a forward path for v2+
# if we ever need to widen the claim again.
_PAYLOAD_VERSION = "v1"

# In-process cache of (user_id -> (epoch, inserted_at_monotonic)). Bounded
# by natural TTL — the WS token itself only lives for 60s, so a stale
# epoch reading is at most one-token-lifetime behind. Five seconds is
# short enough that a sign-out-everywhere takes effect within the WS
# heartbeat window without making every verify a DB round-trip.
_EPOCH_CACHE_TTL_S = 5.0
_EPOCH_CACHE: dict[str, tuple[int, float]] = {}


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


def _build_payload(*, session_id: str, user_id: str, epoch: int, expires_at: int) -> bytes:
    """Serialise the v1 payload as ``v1:<sid>:<uid>:<epoch>:<exp>``.

    Colon-delimited keeps the payload greppable on debug; every component
    is opaque (UUID hex, integers) so there's no need for JSON. The
    version prefix gates the verifier so a future schema change can land
    without ambiguous parsing.
    """
    return f"{_PAYLOAD_VERSION}:{session_id}:{user_id}:{int(epoch)}:{int(expires_at)}".encode()


def _parse_v1_payload(payload: bytes) -> tuple[str, str, int, int]:
    """Return ``(session_id, user_id, epoch, expires_at)`` for a v1 payload."""
    parts = payload.decode("utf-8").split(":")
    if len(parts) != 5 or parts[0] != _PAYLOAD_VERSION:
        raise WsTokenError("unsupported payload version")
    sid, uid, epoch_str, exp_str = parts[1], parts[2], parts[3], parts[4]
    try:
        epoch = int(epoch_str)
        expires_at = int(exp_str)
    except ValueError as exc:
        raise WsTokenError("malformed payload") from exc
    return sid, uid, epoch, expires_at


def issue_ws_token(
    session_id: str,
    *,
    user_id: str,
    epoch: int,
    secret: str | None = None,
) -> str:
    """Return an HMAC-signed token bound to (session_id, user_id, epoch).

    ``epoch`` is the user's current ``session_epoch`` at issue time.
    ``verify_ws_token`` will compare it against the row's CURRENT epoch
    and refuse any token whose claim is older — that's how sign-out-
    everywhere kills already-live WS connections.
    """
    settings_secret = secret or get_settings().session_secret
    expires_at = int(time.time()) + _TOKEN_TTL_S
    payload = _build_payload(
        session_id=session_id,
        user_id=str(user_id),
        epoch=int(epoch),
        expires_at=expires_at,
    )
    return f"{_b64encode(payload)}.{_sign(payload, settings_secret)}"


def _verify_signature_and_parse(token: str, secret: str) -> tuple[str, str, int, int]:
    """Verify HMAC + return the parsed v1 claim or raise."""
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

    return _parse_v1_payload(payload)


def verify_ws_token(token: str, session_id: str, secret: str | None = None) -> bool:
    """Return True if ``token`` is a valid, non-expired, current-epoch claim.

    Four checks, in order:

      1. HMAC signature matches.
      2. Payload is v1 (older / forged versions are rejected).
      3. ``expires_at`` is in the future and ``session_id`` matches.
      4. The token's ``epoch`` claim is >= ``users.session_epoch`` for
         the bound user. A stale claim means the user has signed out of
         every device (or completed an email change, or scheduled their
         account for deletion) since the token was minted — we refuse it
         so the WS connection cannot survive an account-state rotation.

    The user-row lookup is cached for ``_EPOCH_CACHE_TTL_S`` seconds to
    keep chatty terminals off the DB hot path.
    """
    settings_secret = secret or get_settings().session_secret
    try:
        sid_in_token, user_id, claim_epoch, expires_at = _verify_signature_and_parse(
            token, settings_secret
        )
    except WsTokenError:
        return False

    if sid_in_token != session_id:
        return False
    if expires_at < int(time.time()):
        return False

    # Defensive: a malformed user_id (non-UUID) shouldn't ever land here
    # because issue_ws_token only takes a str representation of the
    # caller's authenticated UUID. Treat it as a bad token rather than
    # a 500.
    try:
        uuid.UUID(user_id)
    except (TypeError, ValueError):
        return False

    current_epoch = _load_current_epoch(user_id)
    if current_epoch is None:
        # User row missing — equivalent to a deleted account; refuse.
        return False
    return int(claim_epoch) >= int(current_epoch)


def refresh_ws_token(
    old_token: str,
    session_id: str,
    db: object | None = None,  # accepted for symmetry with REST handlers
    *,
    secret: str | None = None,
    grace_seconds: int = _REFRESH_GRACE_S,
) -> str:
    """Validate ``old_token`` and reissue a fresh token for ``session_id``.

    ``old_token`` may be up to ``grace_seconds`` past its ``expires_at``
    — this is the clock-skew window. The signature itself MUST still
    verify, the session id baked into the token MUST match the requested
    session id, AND the claim's epoch must still be current for the
    bound user (else the user has signed out everywhere since the token
    was minted and we refuse to mint a successor).

    The reissued token is bound to the user's *current* epoch, never the
    stale value carried by the incoming token. Without this, a refresh
    after a sign-out-everywhere would silently re-mint a perpetual
    token against the old epoch — the exact regression the Phase-3
    audit caught.

    The ``db`` parameter is accepted (but currently unused) so callers
    in the REST layer can keep a single dependency-injection shape.
    """
    settings_secret = secret or get_settings().session_secret
    sid_in_token, user_id, claim_epoch, expires_at = _verify_signature_and_parse(
        old_token, settings_secret
    )

    if sid_in_token != session_id:
        raise WsTokenError("session id mismatch")

    now = int(time.time())
    if expires_at + grace_seconds < now:
        raise WsTokenError("token expired beyond grace window")

    current_epoch = _load_current_epoch(user_id)
    if current_epoch is None:
        raise WsTokenError("user not found")
    if int(claim_epoch) < int(current_epoch):
        raise WsTokenError("session epoch rotated")

    return issue_ws_token(
        session_id,
        user_id=user_id,
        epoch=int(current_epoch),
        secret=settings_secret,
    )


def _load_current_epoch(user_id: str) -> int | None:
    """Return ``users.session_epoch`` for ``user_id`` — cached for 5s.

    Returns ``None`` when the user row is missing (deleted account) so
    callers can refuse the token. Cache TTL keeps a flood of WS frames
    from generating one DB round-trip per frame; five seconds is well
    under any human-perceptible "sign-out should kick me out by now"
    latency.
    """
    cached = _EPOCH_CACHE.get(user_id)
    if cached is not None:
        epoch, inserted_at = cached
        if (time.monotonic() - inserted_at) <= _EPOCH_CACHE_TTL_S:
            return epoch
        # Stale — drop and re-fetch below.
        _EPOCH_CACHE.pop(user_id, None)

    fresh = _fetch_epoch_from_db(user_id)
    if fresh is not None:
        _EPOCH_CACHE[user_id] = (int(fresh), time.monotonic())
    return fresh


def _fetch_epoch_from_db(user_id: str) -> int | None:
    """Synchronous wrapper around the async DB lookup.

    ``verify_ws_token`` is called from both async (WS upgrade) and sync
    contexts (tests, the rate limiter middleware). We bridge with
    ``asyncio.run`` when no loop is running, and with a thread when one
    is — the lookup is short and bounded, so a thread hop is cheaper
    than refactoring every caller to be async.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_load_epoch_async(user_id))

    # Loop is running — defer to a worker thread so we don't deadlock on
    # ``run_until_complete``. ``asyncio.run`` in the thread spins up its
    # own loop for the brief DB call.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, _load_epoch_async(user_id))
        return fut.result(timeout=5.0)


async def _load_epoch_async(user_id: str) -> int | None:
    """Async DB hit to fetch ``users.session_epoch`` by id."""
    try:
        from sqlalchemy import select

        from app.db.session import AsyncSessionLocal
        from app.models.user import User

        try:
            uid = uuid.UUID(user_id)
        except (TypeError, ValueError):
            return None
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(User.session_epoch).where(User.id == uid))).first()
            if row is None:
                return None
            value = row[0]
            return int(value) if value is not None else 1
    except Exception:  # pragma: no cover — defensive against transient DB hiccups
        # Fail closed: returning None makes the verifier refuse the
        # token, which is safer than silently bypassing the epoch check.
        return None


def clear_epoch_cache() -> None:
    """Test helper — drop the in-process epoch cache."""
    _EPOCH_CACHE.clear()
