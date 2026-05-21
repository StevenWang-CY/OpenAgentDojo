"""Healthz returns 200 with the expected envelope."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_returns_ok(client) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Booleans for db/redis depend on local services but the keys are always present.
    assert "db" in body
    assert "redis" in body
    assert body["sandbox_driver"] in {"local", "docker"}
