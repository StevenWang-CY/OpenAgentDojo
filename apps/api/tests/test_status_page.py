"""Public ``GET /status`` route — human-readable system summary.

This is the page linked from the marketing footer; ``/healthz/ready`` is the
internal probe. Both are driven by the same bounded probe helpers, so we
stub those (as ``test_status_route.py`` does for healthz) to keep the suite
hermetic on dev boxes without Postgres/Redis/S3.
"""

from __future__ import annotations

import pytest

from app import __version__


async def _ok() -> bool:
    return True


async def _fail() -> bool:
    return False


def _stub_probes(monkeypatch, *, db=True, redis=True, s3=True) -> None:
    """Replace the three bounded probes in ``app.healthz``.

    ``app.status.router`` imports the helpers by name from ``app.healthz``,
    which means it captured the *original* references at import time. We have
    to patch the call-site (status.router) too so the stubbing actually
    takes effect.
    """
    from app import healthz as healthz_mod
    from app.status import router as status_mod

    db_fn = _ok if db else _fail
    redis_fn = _ok if redis else _fail
    s3_fn = _ok if s3 else _fail

    monkeypatch.setattr(healthz_mod, "_db_ok_bounded", db_fn)
    monkeypatch.setattr(healthz_mod, "_redis_ok_bounded", redis_fn)
    monkeypatch.setattr(healthz_mod, "_s3_ok_bounded", s3_fn)
    monkeypatch.setattr(status_mod, "_db_ok_bounded", db_fn)
    monkeypatch.setattr(status_mod, "_redis_ok_bounded", redis_fn)
    monkeypatch.setattr(status_mod, "_s3_ok_bounded", s3_fn)


@pytest.mark.asyncio
async def test_status_returns_200(client, monkeypatch) -> None:
    _stub_probes(monkeypatch)
    resp = await client.get("/status")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_status_shape_is_complete(client, monkeypatch) -> None:
    """The documented JSON shape — guard against accidental schema drift."""
    _stub_probes(monkeypatch)
    resp = await client.get("/status")
    body = resp.json()

    # Top-level keys.
    assert set(body.keys()) == {
        "status",
        "components",
        "version",
        "env",
        "uptime_seconds",
        "links",
    }

    # Components — P2-B6 adds sandbox_pool + workers so the FE can render
    # a single-pane verdict without separate calls.
    assert {"api", "database", "redis", "object_storage"}.issubset(
        body["components"].keys()
    )
    assert "sandbox_pool" in body["components"]
    assert "workers" in body["components"]
    for name, comp in body["components"].items():
        assert set(comp.keys()) == {"status", "checked_at"}, name
        assert comp["status"] in {"operational", "degraded", "down"}, name
        # checked_at is an ISO-8601 string we can round-trip.
        from datetime import datetime

        datetime.fromisoformat(comp["checked_at"])

    # Links.
    assert body["links"] == {
        "healthz": "/healthz",
        "ready": "/healthz/ready",
        "docs": "/docs",
    }


@pytest.mark.asyncio
async def test_status_operational_when_all_probes_pass(client, monkeypatch) -> None:
    _stub_probes(monkeypatch, db=True, redis=True, s3=True)
    resp = await client.get("/status")
    body = resp.json()

    assert body["status"] == "operational"
    assert body["components"]["api"]["status"] == "operational"
    assert body["components"]["database"]["status"] == "operational"
    assert body["components"]["redis"]["status"] == "operational"
    assert body["components"]["object_storage"]["status"] == "operational"
    assert body["components"]["sandbox_pool"]["status"] == "operational"
    assert body["components"]["workers"]["status"] == "operational"


@pytest.mark.asyncio
async def test_status_degraded_when_redis_down(client, monkeypatch) -> None:
    _stub_probes(monkeypatch, db=True, redis=False, s3=True)
    resp = await client.get("/status")
    body = resp.json()

    assert body["status"] == "degraded"
    assert body["components"]["api"]["status"] == "operational"
    assert body["components"]["redis"]["status"] == "down"
    # Other components stay green.
    assert body["components"]["database"]["status"] == "operational"
    assert body["components"]["object_storage"]["status"] == "operational"


@pytest.mark.asyncio
async def test_status_reports_uptime_and_version(client, monkeypatch) -> None:
    _stub_probes(monkeypatch)
    resp = await client.get("/status")
    body = resp.json()

    assert isinstance(body["uptime_seconds"], int)
    assert body["uptime_seconds"] >= 0
    assert body["version"] == __version__


@pytest.mark.asyncio
async def test_status_sets_short_lived_cache_control(client, monkeypatch) -> None:
    """Status is intentionally cacheable for 10s to absorb load spikes."""
    _stub_probes(monkeypatch)
    resp = await client.get("/status")
    assert resp.headers.get("Cache-Control") == "public, max-age=10"
