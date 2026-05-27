"""P0-8 — session mode + integrity event endpoint contract.

Covers:
  * Creating a self-study session (default) and a proctored session.
  * Persisting integrity events on proctored sessions.
  * Silently dropping integrity events on self-study sessions.
  * Rejecting unknown integrity kinds with 422.
  * Rate-limiting (60/min/session).
  * Ownership: a stranger cannot post to someone else's integrity endpoint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.sessions.integrity import _reset_bucket

_CSRF = "test-csrf-mode-fixed"


async def _seed_mission_and_user(db_engine) -> tuple[uuid.UUID, str]:
    """Create a User + Mission once per test; returns ``(user_id, mission_id)``."""
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    mission_id = "m-mode"
    async with session_module.AsyncSessionLocal() as db:
        db.add(
            User(
                id=user_id,
                email="proctored@a.local",
                display_name="Proctored",
                handle="proctored",
            )
        )
        db.add(
            Mission(
                id=mission_id,
                title="Mode Mission",
                difficulty="beginner",
                category="cat",
                repo_pack="p",
                initial_commit="abc123de",
                estimated_minutes=10,
                failure_mode="f",
                skills_tested=["s"],
                manifest_sha256="sha",
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        await db.commit()
    return user_id, mission_id


def _cookie(user_id: uuid.UUID) -> str:
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


def _setup_auth(client, user_id: uuid.UUID) -> None:
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(user_id))
    client.cookies.set("arena_csrf", _CSRF)


@pytest.mark.asyncio
async def test_create_session_defaults_to_self_study(client, db_engine) -> None:
    user_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, user_id)

    resp = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["mode"] == "self_study"
    assert body["integrity_signals_count"] == 0


@pytest.mark.asyncio
async def test_create_session_accepts_proctored_mode(client, db_engine) -> None:
    user_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, user_id)

    resp = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id, "mode": "proctored"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["mode"] == "proctored"


@pytest.mark.asyncio
async def test_create_session_rejects_unknown_mode(client, db_engine) -> None:
    user_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, user_id)

    resp = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id, "mode": "tournament"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_integrity_event_persists_for_proctored(client, db_engine) -> None:
    """A tab.blurred posted on a proctored session lands as a row + bumps the counter."""
    user_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, user_id)

    create = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id, "mode": "proctored"},
        headers={"X-CSRF-Token": _CSRF},
    )
    session_id = uuid.UUID(create.json()["id"])
    _reset_bucket(session_id)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/events/integrity",
        json={"kind": "tab.blurred", "payload": {"seconds_visible_before": 12}},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 204, resp.text

    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        events = (
            (
                await db.execute(
                    select(SupervisionEvent).where(
                        SupervisionEvent.session_id == session_id,
                        SupervisionEvent.event_type == "tab.blurred",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].payload == {"seconds_visible_before": 12}

        row = (await db.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
        assert row.integrity_signals_count == 1


@pytest.mark.asyncio
async def test_integrity_event_dropped_for_self_study(client, db_engine) -> None:
    """Self-study sessions accept the call but do NOT persist or count."""
    user_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, user_id)

    create = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id},
        headers={"X-CSRF-Token": _CSRF},
    )
    session_id = uuid.UUID(create.json()["id"])
    _reset_bucket(session_id)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/events/integrity",
        json={"kind": "tab.blurred", "payload": {"seconds_visible_before": 5}},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 204, resp.text

    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        events = (
            (
                await db.execute(
                    select(SupervisionEvent).where(
                        SupervisionEvent.session_id == session_id,
                        SupervisionEvent.event_type == "tab.blurred",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert events == []

        row = (await db.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
        assert row.integrity_signals_count == 0


@pytest.mark.asyncio
async def test_integrity_event_validates_paste_large_shape(client, db_engine) -> None:
    user_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, user_id)
    create = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id, "mode": "proctored"},
        headers={"X-CSRF-Token": _CSRF},
    )
    session_id = uuid.UUID(create.json()["id"])
    _reset_bucket(session_id)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/events/integrity",
        json={
            "kind": "paste.large",
            "payload": {"chars": 800, "target": "agent_chat"},
        },
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 204, resp.text

    # Unknown target collapses to "other".
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/events/integrity",
        json={"kind": "paste.large", "payload": {"chars": 50, "target": "wild"}},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 204, resp.text

    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        events = (
            (
                await db.execute(
                    select(SupervisionEvent).where(
                        SupervisionEvent.session_id == session_id,
                        SupervisionEvent.event_type == "paste.large",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 2
        targets = {e.payload["target"] for e in events}
        assert targets == {"agent_chat", "other"}


@pytest.mark.asyncio
async def test_integrity_event_rejects_unknown_violation_kind(client, db_engine) -> None:
    user_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, user_id)
    create = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id, "mode": "proctored"},
        headers={"X-CSRF-Token": _CSRF},
    )
    session_id = uuid.UUID(create.json()["id"])
    _reset_bucket(session_id)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/events/integrity",
        json={
            "kind": "proctored.violation",
            "payload": {"kind": "screen_share", "detail": "x"},
        },
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_integrity_event_rate_limited(client, db_engine) -> None:
    """Defence-in-depth: the per-session bucket caps at 60/min."""
    user_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, user_id)
    create = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id, "mode": "proctored"},
        headers={"X-CSRF-Token": _CSRF},
    )
    session_id = uuid.UUID(create.json()["id"])
    _reset_bucket(session_id)

    # 60 succeed, 61st throttles. Keep payload trivial — the test exercises
    # the bucket, not the validator.
    for i in range(60):
        ok = await client.post(
            f"/api/v1/sessions/{session_id}/events/integrity",
            json={"kind": "tab.focused", "payload": {"seconds_blurred": i}},
            headers={"X-CSRF-Token": _CSRF},
        )
        assert ok.status_code == 204, ok.text

    throttled = await client.post(
        f"/api/v1/sessions/{session_id}/events/integrity",
        json={"kind": "tab.focused", "payload": {"seconds_blurred": 99}},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert throttled.status_code == 429, throttled.text
    assert throttled.json()["detail"]["code"] == "integrity_rate_limited"
    _reset_bucket(session_id)


@pytest.mark.asyncio
async def test_integrity_event_stranger_gets_403(client, db_engine) -> None:
    """Cross-user ownership enforcement mirrors the rest of /sessions."""
    owner_id, mission_id = await _seed_mission_and_user(db_engine)
    _setup_auth(client, owner_id)
    create = await client.post(
        "/api/v1/sessions",
        json={"mission_id": mission_id, "mode": "proctored"},
        headers={"X-CSRF-Token": _CSRF},
    )
    session_id = uuid.UUID(create.json()["id"])
    _reset_bucket(session_id)

    other_id = uuid.uuid4()
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=other_id, email="stranger@a.local", display_name="X", handle="x"))
        await db.commit()

    _setup_auth(client, other_id)
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/events/integrity",
        json={"kind": "tab.blurred", "payload": {"seconds_visible_before": 1}},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 403, resp.text
