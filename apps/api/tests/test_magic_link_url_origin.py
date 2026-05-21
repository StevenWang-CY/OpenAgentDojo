"""``POST /api/v1/auth/magic-link`` builds the link against settings.web_origin.

Regression — magic-link URLs used to be built from ``request.base_url``
(= the API host). That broke local sign-in because the link path
``/auth/callback`` is a Next.js route on the *web* origin, not a backend
one — so users clicking the link from the email landed on the API's 404.
"""

from __future__ import annotations

import pytest

from app.auth import email as email_module
from app.config import get_settings


@pytest.mark.asyncio
async def test_magic_link_email_uses_web_origin(client_with_db, monkeypatch) -> None:
    """The generated URL has the web_origin as its scheme+host, not the API host."""
    captured: dict[str, str] = {}

    async def fake_send(to_email, magic_url, settings):
        captured["magic_url"] = magic_url
        captured["to_email"] = to_email
        return True

    monkeypatch.setattr(email_module, "send_magic_link_email", fake_send)
    # Also patch in the routes module — it imports the symbol directly.
    from app.auth import routes as auth_routes

    monkeypatch.setattr(auth_routes, "send_magic_link_email", fake_send)

    resp = await client_with_db.post(
        "/api/v1/auth/magic-link",
        json={"email": "origin-check@example.com"},
    )
    assert resp.status_code == 204

    settings = get_settings()
    web_origin = settings.web_origin.rstrip("/")
    assert captured.get("magic_url"), "send_magic_link_email was not invoked"
    assert captured["magic_url"].startswith(web_origin), (
        f"magic URL {captured['magic_url']!r} does not start with web_origin {web_origin!r}"
    )
    assert "/auth/callback?token=" in captured["magic_url"]
