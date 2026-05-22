"""``GET /api/v1/auth/me`` must always return a ``csrf_token`` string.

The ``UserRead`` schema was changed from ``csrf_token: str | None = None``
to ``csrf_token: str`` (required). The route has always set the field, but
the previous Optional made FE call sites defensively branch on a value the
backend guaranteed — and let a future regression silently strip the token
without breaking the OpenAPI contract.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db
from app.main import create_app
from app.models.user import User


@pytest_asyncio.fixture
async def me_user(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"me-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Me Tester",
        handle=f"me-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_me_returns_csrf_token_as_required_string(me_user, db_session) -> None:
    """GET /auth/me always includes csrf_token as a non-empty string."""
    from app.auth.deps import require_auth

    app = create_app()

    async def _override_db():
        yield db_session

    def _as_me() -> User:
        return me_user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _as_me

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/api/v1/auth/me")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "csrf_token" in body, body
    assert isinstance(body["csrf_token"], str), body
    assert body["csrf_token"], "csrf_token must be a non-empty string"
    # When the request carries no cookie, the server MUST issue one.
    assert "set-cookie" in {k.lower(): v for k, v in resp.headers.items()}.keys() or any(
        "arena_csrf" in h[1] for h in resp.headers.raw if h[0].lower() == b"set-cookie"
    )


@pytest.mark.asyncio
async def test_me_reuses_existing_csrf_cookie(me_user, db_session) -> None:
    """When a CSRF cookie is already present, the response body echoes it."""
    from app.auth.csrf import _CSRF_COOKIE_NAME
    from app.auth.deps import require_auth

    app = create_app()

    async def _override_db():
        yield db_session

    def _as_me() -> User:
        return me_user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _as_me

    existing = "a" * 64
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        ac.cookies.set(_CSRF_COOKIE_NAME, existing)
        resp = await ac.get("/api/v1/auth/me")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["csrf_token"] == existing, (
        f"expected existing cookie {existing!r} to be reused, got {body['csrf_token']!r}"
    )


# Silence unused-import lint — the timedelta import documents the intended
# auth contract (cookies are time-bound) but is not exercised in this file.
_ = (datetime, timedelta, UTC)
