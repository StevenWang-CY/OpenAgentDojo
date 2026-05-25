"""GitHub OAuth identity verification — end-to-end route coverage (P0-7).

Covers:

  a) ``GET /auth/github/start`` returns 503 when OAuth is disabled.
  b) ``GET /auth/github/start`` sets the state cookie + 302s when enabled.
  c) ``GET /auth/github/callback`` rejects a mismatched state.
  d) ``GET /auth/github/callback`` rejects an expired state.
  e) Callback (new user) — creates a fresh row with github_id stamped.
  f) Callback links to an existing email-only user (no github_id).
  g) Callback merges when a row already has the github_id.

GitHub's HTTP endpoints are stubbed via ``monkeypatch`` on
``app.auth.github_oauth.exchange_code_for_token`` and ``fetch_user_profile``
so the tests are deterministic and offline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth import github_oauth
from app.auth.github_oauth import OAUTH_STATE_COOKIE_NAME
from app.config import get_settings
from app.models.user import User
from app.schemas.auth import GithubProfile

# ---------------------------------------------------------------------------
# Phase 4.A.6 isolation — flush any oauth state nonce keys before every
# test so the new single-use defence doesn't reject a fixed test nonce
# that was already consumed by a previous run (TTL is 10 min in Redis).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _flush_oauth_nonce_keys():
    """Drop the OAuth state nonce SETNX keys before every test.

    The Phase 4.A.6 defence stores ``auth:oauth_state_nonce:{nonce}``
    with a 600s TTL on first consume. Test fixtures use literal nonces
    (``link-nonce`` etc.) so two pytest runs within the TTL would
    otherwise see the second run's first callback rejected as a
    replay. Flushing the keys at fixture setup keeps the tests
    independent of Redis state from prior runs.
    """
    import asyncio

    async def _flush():
        try:
            from app.sessions.events import _reset_redis_cache, get_redis

            _reset_redis_cache()
            redis = await get_redis()
            if redis is None:
                return
            # Best-effort SCAN+DEL; keep it inside try because tests
            # without a live Redis treat this as a no-op.
            try:
                async for key in redis.scan_iter("auth:oauth_state_nonce:*"):
                    await redis.delete(key)
            except Exception:
                return
        except Exception:
            return

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_flush())
        loop.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enable_github_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``settings.github_oauth_enabled`` to True for the test.

    The settings object is cached by ``get_settings()``; mutating the
    underlying attributes via ``monkeypatch.setattr`` is the cheapest way
    to flip the feature flag without rebuilding the app. We clear the
    cache afterwards so subsequent tests see the original env-backed
    values.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "github_oauth_client_id", "test-client-id")
    monkeypatch.setattr(settings, "github_oauth_client_secret", "test-client-secret")


def _disable_github_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``settings.github_oauth_enabled`` to False for the test."""
    settings = get_settings()
    monkeypatch.setattr(settings, "github_oauth_client_id", None)
    monkeypatch.setattr(settings, "github_oauth_client_secret", None)


def _make_state_jwt(
    *, nonce: str, exp_offset_seconds: int = 600, return_to: str | None = None
) -> str:
    """Hand-craft an OAuth state JWT keyed by the test session_secret."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_offset_seconds)).timestamp()),
    }
    if return_to is not None:
        payload["return_to"] = return_to
    return jwt.encode(payload, settings.session_secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# (a) start returns 503 when disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_503_when_disabled(client_with_db, monkeypatch) -> None:
    _disable_github_oauth(monkeypatch)
    resp = await client_with_db.get("/api/v1/auth/github/start", follow_redirects=False)
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "oauth_unavailable"


@pytest.mark.asyncio
async def test_availability_endpoint_reports_disabled(client_with_db, monkeypatch) -> None:
    _disable_github_oauth(monkeypatch)
    resp = await client_with_db.get("/api/v1/auth/github/available")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


# ---------------------------------------------------------------------------
# (b) start returns 302 + sets cookie when enabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_sets_cookie_and_redirects_when_enabled(client_with_db, monkeypatch) -> None:
    _enable_github_oauth(monkeypatch)
    resp = await client_with_db.get("/api/v1/auth/github/start", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=test-client-id" in location
    assert "scope=read%3Auser+user%3Aemail" in location
    # State cookie set
    set_cookie = resp.headers.get("set-cookie") or ""
    assert OAUTH_STATE_COOKIE_NAME in set_cookie


@pytest.mark.asyncio
async def test_availability_endpoint_reports_enabled(client_with_db, monkeypatch) -> None:
    _enable_github_oauth(monkeypatch)
    resp = await client_with_db.get("/api/v1/auth/github/available")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}


# ---------------------------------------------------------------------------
# (c) callback rejects mismatched state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_rejects_mismatched_state(client_with_db, monkeypatch) -> None:
    _enable_github_oauth(monkeypatch)
    settings = get_settings()
    cookie_jwt = _make_state_jwt(nonce="cookie-nonce")
    # Query-string state intentionally differs from cookie.
    other_jwt = _make_state_jwt(nonce="url-nonce")
    resp = await client_with_db.get(
        "/api/v1/auth/github/callback",
        params={"code": "fake-code", "state": other_jwt},
        cookies={OAUTH_STATE_COOKIE_NAME: cookie_jwt},
        follow_redirects=False,
    )
    # Failure redirects to the sign-in page with the generic error param.
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location == (f"{settings.web_origin.rstrip('/')}/auth/sign-in?error=github_oauth_failed")


# ---------------------------------------------------------------------------
# (d) callback rejects expired state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_rejects_expired_state(client_with_db, monkeypatch) -> None:
    _enable_github_oauth(monkeypatch)
    expired_jwt = _make_state_jwt(nonce="x", exp_offset_seconds=-60)
    resp = await client_with_db.get(
        "/api/v1/auth/github/callback",
        params={"code": "fake-code", "state": expired_jwt},
        cookies={OAUTH_STATE_COOKIE_NAME: expired_jwt},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "error=github_oauth_failed" in location
    # The error param ends the URL with the typed code (FE renders a toast).
    assert location.endswith("error=github_oauth_failed")


# ---------------------------------------------------------------------------
# (e) callback success — new user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_creates_new_user(client_with_db, db_engine, monkeypatch) -> None:
    _enable_github_oauth(monkeypatch)
    settings = get_settings()
    state_jwt = _make_state_jwt(nonce="new-user-nonce")

    async def fake_exchange(code: str, _settings) -> str:
        assert code == "the-code"
        return "fake-access-token"

    async def fake_profile(token: str) -> GithubProfile:
        assert token == "fake-access-token"
        return GithubProfile(
            github_id=9001,
            login="newuser",
            name="New User",
            avatar_url="https://avatars.githubusercontent.com/u/9001",
            html_url="https://github.com/newuser",
            email="new-user@example.com",
        )

    monkeypatch.setattr(github_oauth, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(github_oauth, "fetch_user_profile", fake_profile)
    # The route module imported the symbols directly, so patch there too.
    import app.auth.routes as auth_routes

    monkeypatch.setattr(auth_routes, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(auth_routes, "fetch_user_profile", fake_profile)

    resp = await client_with_db.get(
        "/api/v1/auth/github/callback",
        params={"code": "the-code", "state": state_jwt},
        cookies={OAUTH_STATE_COOKIE_NAME: state_jwt},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location == f"{settings.web_origin.rstrip('/')}/missions"

    # Verify the row landed in the DB.
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_local() as db:
        row = (
            await db.execute(select(User).where(User.email == "new-user@example.com"))
        ).scalar_one()
        assert row.github_id == 9001
        assert row.github_login == "newuser"
        assert row.github_html_url == "https://github.com/newuser"
        assert row.github_verified_at is not None
        assert row.display_name == "New User"


# ---------------------------------------------------------------------------
# (f) callback links to an existing email-only user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_links_existing_email_only_user(
    client_with_db, db_engine, monkeypatch
) -> None:
    _enable_github_oauth(monkeypatch)
    # Seed an email-only user (no github_id).
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_local() as db:
        seed = User(email="existing@example.com", handle="existing")
        db.add(seed)
        await db.commit()
        seed_id = seed.id

    state_jwt = _make_state_jwt(nonce="link-nonce")

    async def fake_exchange(code: str, _settings) -> str:
        return "tok"

    async def fake_profile(token: str) -> GithubProfile:
        return GithubProfile(
            github_id=4242,
            login="existing-handle",
            name=None,
            avatar_url=None,
            html_url="https://github.com/existing-handle",
            email="existing@example.com",
        )

    import app.auth.routes as auth_routes

    monkeypatch.setattr(auth_routes, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(auth_routes, "fetch_user_profile", fake_profile)

    resp = await client_with_db.get(
        "/api/v1/auth/github/callback",
        params={"code": "code", "state": state_jwt},
        cookies={OAUTH_STATE_COOKIE_NAME: state_jwt},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    async with session_local() as db:
        row = (await db.execute(select(User).where(User.id == seed_id))).scalar_one()
        assert row.github_id == 4242
        assert row.github_login == "existing-handle"
        assert row.github_html_url == "https://github.com/existing-handle"
        assert row.github_verified_at is not None
        # Handle preserved — we never rewrite an existing user's handle.
        assert row.handle == "existing"


# ---------------------------------------------------------------------------
# (g) callback merges when github_id already exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_merges_when_github_id_exists(
    client_with_db, db_engine, monkeypatch
) -> None:
    _enable_github_oauth(monkeypatch)
    # Seed a row that already has the github_id (and a different login —
    # the user renamed on github.com).
    old_verified_at = datetime(2024, 1, 1, tzinfo=UTC)
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_local() as db:
        seed = User(
            email="merged@example.com",
            handle="merged",
            github_id=7777,
            github_login="old-login",
            github_html_url="https://github.com/old-login",
            github_verified_at=old_verified_at,
        )
        db.add(seed)
        await db.commit()
        seed_id = seed.id

    state_jwt = _make_state_jwt(nonce="merge-nonce")

    async def fake_exchange(code: str, _settings) -> str:
        return "tok"

    async def fake_profile(token: str) -> GithubProfile:
        return GithubProfile(
            github_id=7777,
            login="new-login",  # user renamed on github.com
            name=None,
            avatar_url="https://avatars.githubusercontent.com/u/7777",
            html_url="https://github.com/new-login",
            email="merged@example.com",
        )

    import app.auth.routes as auth_routes

    monkeypatch.setattr(auth_routes, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(auth_routes, "fetch_user_profile", fake_profile)

    resp = await client_with_db.get(
        "/api/v1/auth/github/callback",
        params={"code": "code", "state": state_jwt},
        cookies={OAUTH_STATE_COOKIE_NAME: state_jwt},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    async with session_local() as db:
        row = (await db.execute(select(User).where(User.id == seed_id))).scalar_one()
        # Login refreshed.
        assert row.github_login == "new-login"
        assert row.github_html_url == "https://github.com/new-login"
        # verified_at bumped forward.
        verified_at = row.github_verified_at
        assert verified_at is not None
        # Normalise tzinfo for SQLite (naive) vs Postgres (aware) comparison.
        if verified_at.tzinfo is None:
            verified_at = verified_at.replace(tzinfo=UTC)
        assert verified_at > old_verified_at
