"""Integration tests — exercises the FastAPI surface end-to-end via httpx."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def client() -> AsyncClient:
    app = create_app()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_healthz(client: AsyncClient) -> None:
    async with client as c:
        res = await c.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


async def test_calc_price_two_units(client: AsyncClient) -> None:
    async with client as c:
        res = await c.post("/calc/price", json={"qty": 2, "unit": "10.00"})
    assert res.status_code == 200
    assert res.json() == {"total": "20.00"}


async def test_format_ts_tokyo(client: AsyncClient) -> None:
    async with client as c:
        res = await c.post(
            "/format/ts",
            json={"ts": 1768478400, "tz": "Asia/Tokyo"},
        )
    assert res.status_code == 200
    assert res.json() == {"formatted": "2026-01-15 21:00"}
