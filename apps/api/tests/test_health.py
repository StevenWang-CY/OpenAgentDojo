"""Healthz returns 200 with the expected envelope."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_returns_ok(client) -> None:
    """``/healthz`` is the cheap liveness probe — no DB/Redis touches."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["sandbox_driver"] in {"local", "docker"}
    # version + env are part of the new lightweight envelope
    assert "version" in body
    assert "env" in body


@pytest.mark.asyncio
async def test_healthz_ready_still_probes_externals(client) -> None:
    """``/healthz/ready`` keeps the heavyweight DB+Redis probes."""
    resp = await client.get("/healthz/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert "db" in body
    assert "redis" in body
