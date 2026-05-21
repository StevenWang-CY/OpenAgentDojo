"""Rate-limit middleware caps magic-link requests per IP."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_rate_limit_buckets(monkeypatch):
    """Reset the rate-limit middleware's redis + in-memory state per test.

    The middleware persists counters to Redis when reachable; if a previous
    test run within the same minute already hit the cap, this test would
    spuriously fail on its first request. We:

    - point REDIS_URL at an unreachable port to force the in-memory path, and
    - clear ``Settings`` so the override is picked up.
    """
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_magic_link_blocks_after_five_requests_per_minute(client_with_db) -> None:
    """5 magic-link requests from the same IP succeed; the 6th returns 429."""
    payload = {"email": "rate-limit-test@example.com"}

    # Five hits should pass the limiter (even if delivery fails for env reasons,
    # the middleware fires before the handler).
    for i in range(5):
        resp = await client_with_db.post("/api/v1/auth/magic-link", json=payload)
        assert resp.status_code != 429, f"hit {i + 1} unexpectedly rate-limited"

    # Sixth call in the same minute window MUST be rejected.
    sixth = await client_with_db.post("/api/v1/auth/magic-link", json=payload)
    assert sixth.status_code == 429
    body = sixth.json()
    assert body["code"] == "rate_limited"
    assert sixth.headers.get("retry-after") is not None
