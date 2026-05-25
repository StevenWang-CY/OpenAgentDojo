"""Phase 4.A.T3 — the resend throttle stamps BEFORE the SMTP dispatch.

Before Phase 4.A.5, the throttle was stamped AFTER ``send_magic_link_email``
returned — which meant two concurrent ``/auth/magic-link/resend`` calls
landing inside the backend's send window both saw an empty throttle,
both proceeded past the gate, and the user received two duplicate
emails. After the fix the throttle is stamped immediately after the
commit and before any SMTP await, so the second concurrent call
short-circuits with ``wait_seconds > 0``.

Test setup: monkeypatch ``send_magic_link_email`` with an async fake
that sleeps long enough to overlap two requests. Fire both, await
both, and assert only the first dispatched. The second call must see
the throttle.
"""

from __future__ import annotations

import asyncio

import pytest

from app.auth import email as email_module
from app.config import get_settings


@pytest.fixture(autouse=True)
def _force_redis_unavailable(monkeypatch):
    """Pin Redis off so the throttle uses the DB-fallback path.

    Same pattern as ``test_magic_link_resend`` — the conftest's default
    REDIS_URL points at localhost which usually isn't running under
    pytest. We make it explicit here so a test runner with a live
    Redis side-car still produces deterministic behaviour.
    """
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    from app.sessions import events as events_module

    events_module._reset_redis_cache()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_concurrent_resends_only_dispatch_once(client_with_db, monkeypatch) -> None:
    """Two overlapping ``/magic-link/resend`` calls must dispatch exactly once."""
    send_calls: list[str] = []

    async def slow_send(to_email, magic_url, settings):
        # 500ms inside the SMTP backend — long enough to overlap two
        # requests landing back-to-back.
        send_calls.append(to_email)
        await asyncio.sleep(0.5)
        return True

    monkeypatch.setattr(email_module, "send_magic_link_email", slow_send)
    from app.auth import routes as auth_routes

    monkeypatch.setattr(auth_routes, "send_magic_link_email", slow_send)

    email = "race-test@example.com"
    payload = {"email": email}

    # Fire two requests concurrently. The first should commit its token
    # row and stamp the throttle BEFORE awaiting the slow send. The
    # second hits the throttle and returns wait_seconds > 0.
    task_a = asyncio.create_task(
        client_with_db.post("/api/v1/auth/magic-link/resend", json=payload)
    )
    # Stagger by a tiny amount so the second request lands after the
    # first has committed but while the first is still inside ``slow_send``.
    await asyncio.sleep(0.05)
    task_b = asyncio.create_task(
        client_with_db.post("/api/v1/auth/magic-link/resend", json=payload)
    )

    resp_a, resp_b = await asyncio.gather(task_a, task_b)

    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text

    # Exactly ONE dispatch — the second request was throttled.
    assert len(send_calls) == 1, f"expected single dispatch, got {len(send_calls)}: {send_calls}"

    # At least one of the two responses must have surfaced a positive
    # wait. (The first might race and report wait=0 if its body completes
    # before the second request even starts; we accept either of the two
    # carrying the throttle signal.)
    waits = sorted([resp_a.json()["wait_seconds"], resp_b.json()["wait_seconds"]])
    assert waits[1] > 0, (
        f"expected the second concurrent request to be throttled; got waits={waits}"
    )
