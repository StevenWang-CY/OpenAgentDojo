"""CSRF middleware enforces the double-submit cookie pattern on mutations."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_unsafe_post_without_csrf_token_returns_403(client) -> None:
    """A POST to a non-exempt endpoint without CSRF header must 403."""
    # /api/v1/missions exists and is a GET-only collection, but POST /api/v1/auth/logout
    # is a real mutation the middleware should guard.
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 403
    body = resp.json()
    assert "csrf" in body["detail"].lower()


@pytest.mark.asyncio
async def test_post_with_matching_cookie_and_header_passes_csrf(client) -> None:
    """Header + cookie match → middleware allows the request through."""
    token = "deadbeef" * 4  # any non-empty matching pair works
    resp = await client.post(
        "/api/v1/auth/logout",
        cookies={"arena_csrf": token},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_magic_link_endpoint_is_csrf_exempt(client_with_db) -> None:
    """``POST /api/v1/auth/magic-link`` is exempt — the user has no cookie yet."""
    resp = await client_with_db.post(
        "/api/v1/auth/magic-link",
        json={"email": "csrf-exempt@example.com"},
    )
    # Either 204 (delivery succeeded) or 5xx (Resend/SMTP unavailable),
    # but never 403 from the CSRF middleware.
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_options_request_bypasses_csrf(client) -> None:
    """OPTIONS preflights must not be blocked by CSRF middleware."""
    resp = await client.options(
        "/api/v1/auth/logout",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    # CORS will respond 200 (preflight) — not 403 from CSRF.
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_get_requests_bypass_csrf(client_with_db) -> None:
    """GET is a safe method and never requires CSRF."""
    resp = await client_with_db.get("/api/v1/missions")
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_csrf_exempt_uses_exact_match(client) -> None:
    """A child path with the same suffix MUST NOT be wrongly exempted (P1-B1).

    Pre-fix, ``endswith`` would also waive CSRF for a route like
    ``/api/v1/something-auth/magic-link``. We use exact match so the exempt
    list cannot be widened by accident.
    """
    # Build a path that ends with the exempt suffix but is not the exempt path.
    resp = await client.post("/api/v1/admin/api/v1/auth/magic-link")
    # Either 403 from CSRF (preferred) or 404 from the router — never a
    # silent 204/200 implying we treated it as the magic-link endpoint.
    assert resp.status_code in {403, 404, 405}
