"""Demo-user seed script (``app.scripts.seed_demo_users``) — idempotence test.

Verifies:
- Two runs produce the same row count (idempotent upsert).
- All three demo handles are reachable via ``GET /api/v1/profiles/{handle}``.
- Each demo user has at least 2 graded sessions in the response history.
- The seed refuses to run when ``ARENA_ENV=production``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import session as session_module
from app.db.base import Base
from app.models.badge import Badge
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User

_DEMO_HANDLES = ("alice", "bob", "carol")


async def _bind_engine(db_engine):
    """Point ``AsyncSessionLocal`` at the test engine and prime the schema."""
    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_module.AsyncSessionLocal


async def _prime_catalog(session_factory) -> None:
    """Insert just enough mission + badge rows for the seed to attach to."""
    async with session_factory() as db:
        for mid in (
            "auth-cookie-expiration",
            "agent-wrong-file",
            "security-validation-removed",
            "api-contract-drift",
            "missing-regression-test",
            "overfitted-test-fix",
            "async-race-condition",
            "excessive-rewrite",
            "dependency-misuse",
        ):
            db.add(
                Mission(
                    id=mid,
                    title=mid.replace("-", " ").title(),
                    difficulty="intermediate",
                    category="auth",
                    repo_pack="fullstack-auth-demo",
                    initial_commit="HEAD",
                    estimated_minutes=20,
                    failure_mode="placeholder",
                    skills_tested=["test"],
                    manifest_sha256=f"sha-{mid}",
                    version=1,
                    published=True,
                    expected_weak_dim="safety",
                )
            )
        for bid in (
            "regression-test-writer",
            "security-aware-reviewer",
            "agent-skeptic",
            "api-contract-guardian",
            "concurrency-debugger",
            "minimal-diff",
        ):
            db.add(
                Badge(
                    id=bid,
                    title=bid.replace("-", " ").title(),
                    description="demo",
                    icon="shield",
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_seed_creates_three_demo_users(client, db_engine, monkeypatch) -> None:
    """Single run installs the three documented demo profiles."""
    session_factory = await _bind_engine(db_engine)
    await _prime_catalog(session_factory)
    monkeypatch.setenv("ARENA_ENV", "development")

    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    from app.scripts.seed_demo_users import seed_demo_users

    sessions_written = await seed_demo_users()
    assert sessions_written >= 2 * len(_DEMO_HANDLES), "expected at least 2 sessions per demo user"

    async with session_factory() as db:
        rows = (
            (await db.execute(select(User.handle).where(User.handle.in_(_DEMO_HANDLES))))
            .scalars()
            .all()
        )
    assert set(rows) == set(_DEMO_HANDLES)


@pytest.mark.asyncio
async def test_seed_is_idempotent(client, db_engine, monkeypatch) -> None:
    """A second run reuses existing users and replaces their session history."""
    session_factory = await _bind_engine(db_engine)
    await _prime_catalog(session_factory)
    monkeypatch.setenv("ARENA_ENV", "development")

    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    from app.scripts.seed_demo_users import seed_demo_users

    first_total = await seed_demo_users()
    second_total = await seed_demo_users()
    assert first_total == second_total

    async with session_factory() as db:
        user_count = (
            await db.execute(select(func.count(User.id)).where(User.handle.in_(_DEMO_HANDLES)))
        ).scalar_one()
        session_count = (
            await db.execute(
                select(func.count(SessionRow.id)).where(
                    SessionRow.user_id.in_(select(User.id).where(User.handle.in_(_DEMO_HANDLES)))
                )
            )
        ).scalar_one()
    assert user_count == 3
    assert session_count == first_total


@pytest.mark.asyncio
async def test_seed_refuses_in_production(monkeypatch) -> None:
    """The seed must never run in production.

    We monkey-patch the settings function rather than flipping ARENA_ENV via
    env vars because the production Settings validator requires several other
    secrets (RESEND_API_KEY, S3 keys, etc.) that are unrelated to this guard.
    """
    from app.scripts import seed_demo_users as seed_mod

    class _FakeSettings:
        arena_env = "production"

    def _fake_get_settings() -> _FakeSettings:
        return _FakeSettings()

    monkeypatch.setattr(seed_mod, "get_settings", _fake_get_settings)

    with pytest.raises(RuntimeError, match="production"):
        await seed_mod.seed_demo_users()


@pytest.mark.asyncio
async def test_seeded_profiles_are_reachable(client, db_engine, monkeypatch) -> None:
    """After seeding, ``GET /api/v1/profiles/{handle}`` returns 200 for each demo user."""
    session_factory = await _bind_engine(db_engine)
    await _prime_catalog(session_factory)
    monkeypatch.setenv("ARENA_ENV", "development")

    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    from app.scripts.seed_demo_users import seed_demo_users

    await seed_demo_users()

    for handle in _DEMO_HANDLES:
        resp = await client.get(f"/api/v1/profiles/{handle}")
        assert resp.status_code == 200, f"{handle}: {resp.text}"
        body = resp.json()
        assert body["handle"] == handle
        assert body["display_name"]
        assert body["total_missions"] >= 2
        assert len(body["history"]) >= 2
        # Radar averages must include at least the seven rubric dimensions
        # the score helper distributed across.
        assert set(body["radar_averages"].keys()) == {
            "final_correctness",
            "verification",
            "agent_review",
            "prompt_quality",
            "context_selection",
            "safety",
            "diff_minimality",
        }
