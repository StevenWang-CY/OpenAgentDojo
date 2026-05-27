"""Per-user concurrency cap — §21 MVP: 1 active session per user.

The cap is enforced at the service layer (``create_session``) and surfaced
through the router as ``409 Conflict`` with an ``active_session_id`` payload
so the frontend can deep-link the user back to their existing session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User


@pytest.fixture(autouse=True)
def _force_in_memory_rate_limit(monkeypatch):
    """Stop the rate-limit middleware from talking to a real Redis.

    Without this the per-user bucket persists across test runs and trips the
    `sessions_create` cap (6/min) intermittently.
    """
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def cap_setup(client, db_engine, monkeypatch):
    """Seed two users + one mission and wire the app to the test engine.

    Returns the fully prepared httpx client (with CSRF cookie set) plus the
    user/mission rows for the tests to read ids off.
    """
    import app.workers.provision as provision_mod
    from app.db import session as session_module

    # Share the test engine with the app's session module so the in-process
    # DB writes inside the handler hit the same SQLite as our seed.
    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Stub the provision enqueue so the POST handler doesn't schedule a real
    # background task against the test DB.
    monkeypatch.setattr(provision_mod, "enqueue_provision", lambda _sid: None)

    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_a_id,
                email=f"alice-cap-{uuid.uuid4().hex[:6]}@arena.local",
                display_name="Alice",
            )
        )
        db.add(
            User(
                id=user_b_id,
                email=f"bob-cap-{uuid.uuid4().hex[:6]}@arena.local",
                display_name="Bob",
            )
        )
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Cap test mission",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="abc12345",
                estimated_minutes=10,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="c" * 64,
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        await db.commit()

    return {
        "client": client,
        "user_a_id": user_a_id,
        "user_b_id": user_b_id,
        "mission_id": "auth-cookie-expiration",
    }


def _sign_session_for(user_id: uuid.UUID) -> str:
    from app.auth.session_cookie import _ALGORITHM
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


def _auth_as(client, user_id: uuid.UUID) -> str:
    """Set session + CSRF cookies for ``user_id`` and return the CSRF token."""
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _sign_session_for(user_id))
    csrf = "z" * 64
    client.cookies.set("arena_csrf", csrf)
    return csrf


async def _post_session(client, user_id: uuid.UUID, mission_id: str):
    csrf = _auth_as(client, user_id)
    return await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id},
        headers={"X-CSRF-Token": csrf},
    )


@pytest.mark.asyncio
async def test_second_session_for_same_user_returns_409(cap_setup) -> None:
    client = cap_setup["client"]
    user_id = cap_setup["user_a_id"]
    mission_id = cap_setup["mission_id"]

    first = await _post_session(client, user_id, mission_id)
    assert first.status_code == 202, first.text
    first_id = first.json()["id"]

    second = await _post_session(client, user_id, mission_id)
    assert second.status_code == 409, second.text
    detail = second.json().get("detail")
    # Detail carries the conflict envelope as a structured object so the FE
    # Resume CTA can narrow on ``active_session_id`` without falling back
    # to a generic "HTTP 409" toast.
    assert isinstance(detail, dict), second.json()
    assert detail["code"] == "active_session_exists"
    assert detail["active_session_id"] == first_id
    assert detail["message"] == "an active session already exists"
    # Headers retained as advisory for legacy / log scrapers.
    assert second.headers.get("X-Code") == "active_session_exists"
    assert second.headers.get("X-Active-Session-Id") == first_id


@pytest.mark.asyncio
async def test_third_session_succeeds_after_first_is_graded(cap_setup) -> None:
    from app.db import session as session_module

    client = cap_setup["client"]
    user_id = cap_setup["user_a_id"]
    mission_id = cap_setup["mission_id"]

    first = await _post_session(client, user_id, mission_id)
    assert first.status_code == 202, first.text
    first_id = uuid.UUID(first.json()["id"])

    # Second still blocked.
    blocked = await _post_session(client, user_id, mission_id)
    assert blocked.status_code == 409, blocked.text

    # Flip the first session to ``graded`` — cap should release.
    async with session_module.AsyncSessionLocal() as db:
        row = await db.get(SessionRow, first_id)
        row.status = "graded"
        row.completed_at = datetime.now(UTC)
        await db.commit()

    third = await _post_session(client, user_id, mission_id)
    assert third.status_code == 202, third.text
    assert third.json()["id"] != str(first_id)


@pytest.mark.asyncio
async def test_other_users_are_unaffected_by_a_users_active_session(
    cap_setup,
) -> None:
    client = cap_setup["client"]
    user_a_id = cap_setup["user_a_id"]
    user_b_id = cap_setup["user_b_id"]
    mission_id = cap_setup["mission_id"]

    # User A starts a session.
    a_resp = await _post_session(client, user_a_id, mission_id)
    assert a_resp.status_code == 202, a_resp.text

    # User B can still create one — the cap is per-user.
    b_resp = await _post_session(client, user_b_id, mission_id)
    assert b_resp.status_code == 202, b_resp.text
    assert b_resp.json()["user_id"] == str(user_b_id)
