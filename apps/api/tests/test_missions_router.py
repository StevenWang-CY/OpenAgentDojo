"""Mission catalog endpoints."""

from __future__ import annotations

import pytest

from app.db.base import Base
from app.models.mission import Mission


@pytest.mark.asyncio
async def test_list_and_detail(client, db_engine) -> None:
    """Seed Mission 01 directly and exercise both GET routes against the app's DB."""
    # The test client uses the engine from app.db.session, not db_engine. We patch
    # that engine to share schema + connection with our in-memory DB.
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    monkey_engine = db_engine

    # Re-bind AsyncSessionLocal to the test engine.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_module.AsyncSessionLocal = async_sessionmaker(  # type: ignore[assignment]
        bind=monkey_engine, expire_on_commit=False
    )

    # Ensure schema is present.
    async with monkey_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_module.AsyncSessionLocal() as db:
        db.add(
            Mission(
                id="auth-cookie-expiration",
                title="Expired Session Cookie Still Grants Access",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="abc123de",
                estimated_minutes=35,
                failure_mode="checks_presence_not_expiration",
                skills_tested=["auth", "security"],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/missions")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert any(r["id"] == "auth-cookie-expiration" for r in rows)

    detail = await client.get("/api/v1/missions/auth-cookie-expiration")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == "auth-cookie-expiration"
    assert body["category"] == "auth"

    missing = await client.get("/api/v1/missions/does-not-exist")
    assert missing.status_code == 404
