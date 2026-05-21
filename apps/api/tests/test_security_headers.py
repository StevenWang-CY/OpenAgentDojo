"""Every response carries the baseline security headers."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_carries_security_headers(client) -> None:
    """A simple GET picks up all four guard headers."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200

    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert "content-security-policy" in headers
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "permissions-policy" in headers

    # CSP sanity-check.
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors" not in csp or "frame-ancestors 'self'" not in csp
    assert "connect-src" in csp


@pytest.mark.asyncio
async def test_missions_endpoint_carries_security_headers(client_with_db) -> None:
    """Headers attach on JSON responses too — not just /healthz."""
    resp = await client_with_db.get("/api/v1/missions")
    assert resp.status_code == 200
    headers = {k.lower(): v for k, v in resp.headers.items()}
    for required in (
        "content-security-policy",
        "x-frame-options",
        "x-content-type-options",
        "referrer-policy",
        "permissions-policy",
    ):
        assert required in headers, f"missing {required} on /api/v1/missions"
