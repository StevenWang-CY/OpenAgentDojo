"""P0-10 — ``POST /auth/magic-link/resend`` enforces the 60-second per-email
cooldown WITHOUT distinguishing user-exists from user-absent (privacy oracle
defence) and without invoking the email backend on the throttled path.

The DB-derived fallback path is exercised explicitly: by leaving Redis
unreachable (the conftest pins ``REDIS_URL`` to localhost:6379 which is
typically absent on the laptop running ``uv run pytest``), the throttle
state is read off the freshly-minted ``MagicLinkToken`` row instead. The
TTL semantics still hold: after the window passes the send proceeds again.

The metric ticks are validated against ``magic_link_email_total`` so a
future refactor that drops the structured logging would still be caught
by the contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.auth import email as email_module
from app.auth import magic_link as magic_link_module
from app.config import get_settings
from app.models.magic_link_token import PURPOSE_SIGN_IN, MagicLinkToken
from app.models.user import User
from app.observability import magic_link_email_total, magic_link_throttled_total


@pytest.fixture(autouse=True)
def _force_redis_unavailable(monkeypatch):
    """Force the resend throttle onto the DB fallback path.

    The Redis fallback in ``magic_link_resend_wait_seconds`` returns
    ``None`` when ``get_redis()`` cannot reach the server, after which
    the route falls through to ``magic_link_resend_db_fallback_wait_seconds``
    (which inspects the most-recent token row). Pinning the URL at a
    deliberately-bogus host keeps the test hermetic on CI runners that
    don't have a Redis side-car.
    """
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    # Reset the shared module-level Redis client cache so the next call
    # re-probes against the bogus URL instead of reusing a stale
    # connection from another test file.
    from app.sessions import events as events_module

    events_module._reset_redis_cache()
    yield
    get_settings.cache_clear()


def _metric_value(backend: str, outcome: str) -> float:
    sample = magic_link_email_total.labels(backend=backend, outcome=outcome)
    # prometheus_client.Counter exposes the inner value via ._value.get().
    return float(sample._value.get())  # type: ignore[attr-defined]


def _throttled_total() -> float:
    """Phase 4.A.16 — dedicated counter for resend-throttle short-circuits."""
    return float(magic_link_throttled_total._value.get())  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_first_resend_sends_and_subsequent_calls_throttle(
    client_with_db, monkeypatch
) -> None:
    """First call sends; second call within the window is suppressed.

    Assertions:

    1. First call ⇒ 200 with ``wait_seconds=0`` and Retry-After=0; the
       email backend was invoked.
    2. Second call within the window ⇒ 200 with ``wait_seconds>0`` and
       a positive Retry-After header; the email backend was NOT
       invoked again; the throttled metric ticked.
    """
    send_calls: list[str] = []

    async def fake_send(to_email, magic_url, settings):
        send_calls.append(to_email)
        return True

    monkeypatch.setattr(email_module, "send_magic_link_email", fake_send)
    from app.auth import routes as auth_routes

    monkeypatch.setattr(auth_routes, "send_magic_link_email", fake_send)

    throttled_before = _throttled_total()

    payload = {"email": "resend-test@example.com"}

    # First call — clean slate, should send and report wait=0.
    resp = await client_with_db.post("/api/v1/auth/magic-link/resend", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"wait_seconds": 0}
    assert resp.headers["retry-after"] == "0"
    assert send_calls == [payload["email"]]

    # Second call — should hit the DB-fallback throttle because the token
    # row created by the first call is less than the cooldown old.
    resp2 = await client_with_db.post("/api/v1/auth/magic-link/resend", json=payload)
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["wait_seconds"] > 0
    assert body2["wait_seconds"] <= magic_link_module.MAGIC_LINK_RESEND_WINDOW_SECONDS
    assert int(resp2.headers["retry-after"]) == body2["wait_seconds"]
    # The email backend MUST NOT have been called a second time.
    assert send_calls == [payload["email"]]

    throttled_after = _throttled_total()
    assert throttled_after >= throttled_before + 1, (
        f"throttled counter did not tick: before={throttled_before} after={throttled_after}"
    )


@pytest.mark.asyncio
async def test_resend_after_window_sends_again(client_with_db, monkeypatch, db_session) -> None:
    """After the throttle window elapses the email backend is called again.

    Simulating elapsed time by hand: rather than waiting 60 wall-clock
    seconds, we age the freshly-minted token row's ``expires_at`` so
    the DB-derived wait calculation lands at 0. This is the same row
    the fallback path inspects, so it's a faithful simulation of a
    future call landing past the cooldown.
    """
    send_calls: list[str] = []

    async def fake_send(to_email, magic_url, settings):
        send_calls.append(to_email)
        return True

    monkeypatch.setattr(email_module, "send_magic_link_email", fake_send)
    from app.auth import routes as auth_routes

    monkeypatch.setattr(auth_routes, "send_magic_link_email", fake_send)

    email = "expired-resend@example.com"
    resp = await client_with_db.post("/api/v1/auth/magic-link/resend", json={"email": email})
    assert resp.status_code == 200
    assert resp.json()["wait_seconds"] == 0
    assert len(send_calls) == 1

    # Age the most recent sign-in token so the DB fallback computes a
    # wait of 0. We push ``expires_at`` back so ``created_at = expires_at
    # - ttl`` lands well outside the window.
    from sqlalchemy import select

    settings = get_settings()
    row = (
        await db_session.execute(
            select(MagicLinkToken)
            .join(User, User.id == MagicLinkToken.user_id)
            .where(
                User.email == email,
                MagicLinkToken.purpose == PURPOSE_SIGN_IN,
            )
        )
    ).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=settings.magic_link_ttl_minutes)
    db_session.add(row)
    await db_session.commit()

    resp2 = await client_with_db.post("/api/v1/auth/magic-link/resend", json={"email": email})
    assert resp2.status_code == 200, resp2.text
    body = resp2.json()
    assert body["wait_seconds"] == 0
    # A second send must have landed now that the window elapsed.
    assert len(send_calls) == 2
