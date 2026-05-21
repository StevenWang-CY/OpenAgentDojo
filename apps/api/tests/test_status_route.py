"""``GET /healthz/ready`` returns the documented readiness shape.

We bypass real Redis/S3 dependencies by stubbing the bounded probes and use
the ASGI in-process client against the real FastAPI app.
"""

from __future__ import annotations

import pytest

from app import __version__
from app.healthz import router as health_router  # noqa: F401 — import for side effects


@pytest.mark.asyncio
async def test_healthz_ready_returns_expected_shape(client, monkeypatch) -> None:
    # Stub the three external probes so the test is hermetic.
    from app import healthz as healthz_mod

    async def _ok():
        return True

    monkeypatch.setattr(healthz_mod, "_db_ok_bounded", _ok)
    monkeypatch.setattr(healthz_mod, "_redis_ok_bounded", _ok)
    monkeypatch.setattr(healthz_mod, "_s3_ok_bounded", _ok)

    resp = await client.get("/healthz/ready")
    assert resp.status_code == 200
    body = resp.json()

    assert body["db"] is True
    assert body["redis"] is True
    assert body["s3"] is True
    assert body["version"] == __version__
    assert body["sandbox_driver"] in {"local", "docker"}


@pytest.mark.asyncio
async def test_healthz_ready_reports_failure_per_probe(client, monkeypatch) -> None:
    """A failing DB or Redis probe MUST surface as 503 (F2).

    Load balancers and Kubernetes need the explicit failure code so they
    can de-list the pod from the routing pool.
    """
    from app import healthz as healthz_mod

    async def _ok():
        return True

    async def _fail():
        return False

    monkeypatch.setattr(healthz_mod, "_db_ok_bounded", _ok)
    monkeypatch.setattr(healthz_mod, "_redis_ok_bounded", _fail)
    monkeypatch.setattr(healthz_mod, "_s3_ok_bounded", _ok)

    resp = await client.get("/healthz/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["db"] is True
    assert body["redis"] is False
    assert body["s3"] is True


@pytest.mark.asyncio
async def test_healthz_ready_s3_failure_still_200(client, monkeypatch) -> None:
    """An S3 hiccup is best-effort — must NOT take the pod out of rotation."""
    from app import healthz as healthz_mod

    async def _ok():
        return True

    async def _fail():
        return False

    monkeypatch.setattr(healthz_mod, "_db_ok_bounded", _ok)
    monkeypatch.setattr(healthz_mod, "_redis_ok_bounded", _ok)
    monkeypatch.setattr(healthz_mod, "_s3_ok_bounded", _fail)

    resp = await client.get("/healthz/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db"] is True
    assert body["redis"] is True
    assert body["s3"] is False
