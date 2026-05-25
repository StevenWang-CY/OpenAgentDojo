"""Phase 4.A.T9 — magic-link callback honours the persisted ``next_path``.

The Phase 4.A.13 work added a ``next: str | None`` field on
``POST /auth/magic-link``. The route validates it against the FE-route
allowlist and stores the validated value on the token row's
``next_path`` column. The ``GET /auth/callback`` route then redirects
to ``web_origin + next_path`` (or ``/missions`` when no path was
persisted / the value didn't survive a re-validation).

Test: POST a magic-link with ``next=/report/{uuid}``, click the
callback URL the email would have linked, and assert the redirect
Location header matches ``web_origin + /report/{uuid}``.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import email as email_module
from app.config import get_settings


@pytest.fixture(autouse=True)
def _force_redis_unavailable(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    from app.sessions import events as events_module

    events_module._reset_redis_cache()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_callback_redirects_to_next_path(client_with_db, monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_send(to_email, magic_url, settings):
        captured["url"] = magic_url
        return True

    monkeypatch.setattr(email_module, "send_magic_link_email", fake_send)
    from app.auth import routes as auth_routes

    monkeypatch.setattr(auth_routes, "send_magic_link_email", fake_send)

    report_id = str(uuid.uuid4())
    next_path = f"/report/{report_id}"

    resp = await client_with_db.post(
        "/api/v1/auth/magic-link",
        json={"email": "next-test@example.com", "next": next_path},
    )
    assert resp.status_code == 204, resp.text
    assert "url" in captured, "magic-link email was not dispatched"

    # The magic URL is ``{api_base}/auth/callback?token=<raw>``.
    magic_url = captured["url"]
    assert "token=" in magic_url
    # Strip the host prefix so we hit the in-process ASGI directly.
    callback_path = magic_url.split("/auth/callback", 1)[1]
    callback_url = "/api/v1/auth/callback" + callback_path

    redirect_resp = await client_with_db.get(callback_url, follow_redirects=False)
    assert redirect_resp.status_code == 302, redirect_resp.text
    location = redirect_resp.headers["location"]
    settings = get_settings()
    expected = f"{settings.web_origin.rstrip('/')}{next_path}"
    assert location == expected, f"expected callback to redirect to {expected}, got {location}"


@pytest.mark.asyncio
async def test_callback_falls_back_to_missions_for_invalid_next(
    client_with_db, monkeypatch
) -> None:
    """An invalid ``next`` path is dropped at request time; callback uses /missions."""
    captured: dict[str, str] = {}

    async def fake_send(to_email, magic_url, settings):
        captured["url"] = magic_url
        return True

    monkeypatch.setattr(email_module, "send_magic_link_email", fake_send)
    from app.auth import routes as auth_routes

    monkeypatch.setattr(auth_routes, "send_magic_link_email", fake_send)

    resp = await client_with_db.post(
        "/api/v1/auth/magic-link",
        json={
            "email": "fallback-next@example.com",
            "next": "//evil.example.com/path",  # protocol-relative; must be dropped
        },
    )
    assert resp.status_code == 204, resp.text

    magic_url = captured["url"]
    callback_path = magic_url.split("/auth/callback", 1)[1]
    callback_url = "/api/v1/auth/callback" + callback_path

    redirect_resp = await client_with_db.get(callback_url, follow_redirects=False)
    assert redirect_resp.status_code == 302
    location = redirect_resp.headers["location"]
    settings = get_settings()
    assert location == f"{settings.web_origin.rstrip('/')}/missions", (
        f"expected fallback to /missions, got {location}"
    )
