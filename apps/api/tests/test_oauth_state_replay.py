"""Phase 4.A.T4 — OAuth state nonce single-use guard.

The state cookie's JWT carries a ``nonce`` that ``consume_oauth_state``
SETNXes into Redis with the same TTL the cookie has. A second consume
of the same nonce must raise :class:`OAuthStateReplayError`. When
Redis is unavailable the guard degrades to "allow" — see
``test_replay_allows_when_redis_unavailable``.
"""

from __future__ import annotations

import secrets

import pytest

from app.auth import github_oauth as oauth_module
from app.auth.github_oauth import OAuthStateReplayError, consume_oauth_state
from app.config import get_settings


class _FakeRequest:
    def __init__(self, state: str) -> None:
        self.cookies = {oauth_module.OAUTH_STATE_COOKIE_NAME: state}


class _FakeRedis:
    """In-memory stand-in for ``redis.asyncio.Redis`` SETNX semantics."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int = 0, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


@pytest.mark.asyncio
async def test_second_consume_raises_replay_error(monkeypatch) -> None:
    """Two consumes of the same state → second one raises OAuthStateReplayError."""
    settings = get_settings()
    # Mint a real state token with a random nonce so the JWT decodes.
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    nonce = secrets.token_urlsafe(16)
    now = datetime.now(UTC)
    payload = {
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=600)).timestamp()),
    }
    token = jwt.encode(payload, settings.session_secret, algorithm="HS256")

    fake_redis = _FakeRedis()

    async def _get_redis():
        return fake_redis

    monkeypatch.setattr("app.sessions.events.get_redis", _get_redis)

    request = _FakeRequest(state=token)
    # First consume succeeds.
    first = await consume_oauth_state(request, settings, presented_state=token)
    assert first["nonce"] == nonce

    # Second consume must trip the single-use guard.
    with pytest.raises(OAuthStateReplayError):
        await consume_oauth_state(request, settings, presented_state=token)


@pytest.mark.asyncio
async def test_replay_allows_when_redis_unavailable(monkeypatch) -> None:
    """Redis being None must NOT lock the user out (defence-in-depth)."""
    settings = get_settings()
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    nonce = secrets.token_urlsafe(16)
    now = datetime.now(UTC)
    payload = {
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=600)).timestamp()),
    }
    token = jwt.encode(payload, settings.session_secret, algorithm="HS256")

    async def _get_redis_none():
        return None

    monkeypatch.setattr("app.sessions.events.get_redis", _get_redis_none)

    request = _FakeRequest(state=token)
    # Both consumes succeed because the single-use SETNX cannot run.
    first = await consume_oauth_state(request, settings, presented_state=token)
    second = await consume_oauth_state(request, settings, presented_state=token)
    assert first["nonce"] == nonce
    assert second["nonce"] == nonce
