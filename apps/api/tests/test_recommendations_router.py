"""P1-2 — ``GET /api/v1/me/recommendations`` happy-path + auth gate.

Two cases pinned:

* Anonymous callers get 401 — the recommendation surface is personal
  (P1_DESIGN §P1-2 open decisions: "Should the recommendation be
  shown to signed-out catalog visitors? No.").
* Signed-in users with a seeded catalogue get 200 + three items + the
  cold-start diagnosis when their history is empty.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.repo_pack import RepoPack
from app.models.user import User
from app.recommendations.engine import INTRODUCTORY_LADDER


async def _bind_engine(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_catalogue(session_local) -> None:
    """Seed three published missions matching the introductory ladder."""
    async with session_local() as db:
        db.add(
            RepoPack(
                id="fullstack-auth-demo",
                title="TS pack",
                language="typescript",
                stack_summary="ts",
                repo_sha="0" * 40,
            )
        )
        for mid in INTRODUCTORY_LADDER:
            db.add(
                Mission(
                    id=mid,
                    title=f"Mission {mid}",
                    difficulty="beginner",
                    category="testing",
                    repo_pack="fullstack-auth-demo",
                    repo_pack_id="fullstack-auth-demo",
                    initial_commit="abc1234",
                    estimated_minutes=20,
                    failure_mode=mid.replace("-", "_"),
                    skills_tested=[],
                    tags=[],
                    expected_weak_dim="safety",
                    manifest_sha256="0" * 64,
                    version=1,
                    published=True,
                    kind="standard",
                )
            )
        await db.commit()


async def _seed_user(session_local) -> User:
    user_id = uuid.uuid4()
    async with session_local() as db:
        u = User(
            id=user_id,
            email=f"rec-{user_id.hex[:6]}@test.local",
            handle=f"rec-{user_id.hex[:4]}",
            session_epoch=1,
        )
        db.add(u)
        await db.commit()
        return u


def _auth_as(client_with_db, user: User) -> None:
    from app.auth.deps import require_auth

    async def _fake_require_auth() -> User:
        return user

    client_with_db._transport.app.dependency_overrides[require_auth] = (  # type: ignore[attr-defined]
        _fake_require_auth
    )


def _clear_auth(client_with_db) -> None:
    client_with_db._transport.app.dependency_overrides.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401(client_with_db) -> None:
    resp = await client_with_db.get("/api/v1/me/recommendations")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_signed_in_user_gets_three_cold_start_items(
    client_with_db, db_engine
) -> None:
    session_local = await _bind_engine(db_engine)
    await _seed_catalogue(session_local)
    user = await _seed_user(session_local)
    _auth_as(client_with_db, user)
    try:
        resp = await client_with_db.get("/api/v1/me/recommendations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["weakest_dim"] is None
        assert len(body["recommendations"]) == 3
        assert [r["mission_id"] for r in body["recommendations"]] == list(
            INTRODUCTORY_LADDER
        )
        assert body["cache_hit"] is False
    finally:
        _clear_auth(client_with_db)
