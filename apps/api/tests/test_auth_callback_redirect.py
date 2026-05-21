"""``GET /api/v1/auth/callback`` redirects to the configured WEB_ORIGIN."""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.mark.asyncio
async def test_invalid_token_returns_400(client_with_db) -> None:
    """An unknown token cannot pass — the handler returns 400, not a redirect."""
    resp = await client_with_db.get(
        "/api/v1/auth/callback",
        params={"token": "definitely-not-a-real-token"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_redirect_target_is_absolute(client_with_db, db_engine) -> None:
    """After a valid token consume, the Location header is an absolute web_origin URL."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.auth.magic_link import create_magic_link

    settings = get_settings()
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_local() as db:
        magic_url = await create_magic_link(
            db, email="redirect-test@example.com", base_url="http://api.local"
        )
        await db.commit()

    raw_token = magic_url.split("token=", 1)[1]
    resp = await client_with_db.get(
        "/api/v1/auth/callback",
        params={"token": raw_token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(settings.web_origin.rstrip("/")), (
        f"redirect to {location!r} is not under web_origin {settings.web_origin!r}"
    )
    assert location.endswith("/missions")
