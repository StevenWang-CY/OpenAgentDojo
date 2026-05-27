"""P1-1 mission catalog filter endpoints.

Exercises ``GET /api/v1/missions?language=...``, ``?tags=...``,
``?repo_pack=...``, and ``?include=upcoming``. Each filter is an
AND-combined narrowing; ``include=upcoming`` appends ``coming_soon``
entries from ``apps/api/app/missions/roadmap.yaml`` to the response.

These tests seed two missions tied to the seeded ``repo_packs`` rows
(one TS / one Python) so the language filter has a real cross-section
to discriminate.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.repo_pack import RepoPack


async def _bind(client_with_db, db_engine) -> async_sessionmaker:
    """Create schema + return a sessionmaker pointed at the test engine."""
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.db import session as session_module

    return session_module.AsyncSessionLocal  # already rebound by the fixture


def _missions_seed_rows() -> list[Mission]:
    return [
        Mission(
            id="auth-cookie-expiration",
            title="Auth Cookie",
            difficulty="intermediate",
            category="auth",
            repo_pack="fullstack-auth-demo",
            repo_pack_id="fullstack-auth-demo",
            initial_commit="abc12345",
            estimated_minutes=35,
            failure_mode="checks_presence_not_expiration",
            skills_tested=["auth"],
            tags=["checks_presence_not_expiration", "skill:auth", "lang:typescript"],
            expected_weak_dim="safety",
            manifest_sha256="0" * 64,
            version=1,
            published=True,
        ),
        Mission(
            id="overfitted-test-fix",
            title="Overfit",
            difficulty="beginner",
            category="testing",
            repo_pack="data-api-demo",
            repo_pack_id="data-api-demo",
            initial_commit="deadbeef",
            estimated_minutes=25,
            failure_mode="overfitted_visible_test",
            skills_tested=["test-writing"],
            tags=["overfitted_visible_test", "lang:python"],
            expected_weak_dim="safety",
            manifest_sha256="0" * 64,
            version=1,
            published=True,
        ),
    ]


def _repo_packs_seed() -> list[RepoPack]:
    return [
        RepoPack(
            id="fullstack-auth-demo",
            title="TS pack",
            language="typescript",
            stack_summary="stack",
            repo_sha="0" * 40,
        ),
        RepoPack(
            id="data-api-demo",
            title="Py pack",
            language="python",
            stack_summary="stack",
            repo_sha="0" * 40,
        ),
        RepoPack(
            id="go-orders-service",
            title="Go pack",
            language="go",
            stack_summary="stack",
            repo_sha="0" * 40,
        ),
    ]


async def _seed(client_with_db, db_engine) -> None:
    Session = await _bind(client_with_db, db_engine)
    async with Session() as db:
        for pack in _repo_packs_seed():
            db.add(pack)
        await db.flush()
        for mission in _missions_seed_rows():
            db.add(mission)
        await db.commit()


@pytest.mark.asyncio
async def test_unfiltered_list_returns_all_published_missions(
    client_with_db, db_engine
) -> None:
    await _seed(client_with_db, db_engine)
    resp = await client_with_db.get("/api/v1/missions")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert {"auth-cookie-expiration", "overfitted-test-fix"} <= ids
    # Default response carries the new P1-1 fields on every row.
    for row in rows:
        assert "tags" in row
        assert "language" in row
        assert "repo_pack_id" in row
        assert row["status"] == "shipped"


@pytest.mark.asyncio
async def test_language_filter_narrows_to_python_pack(
    client_with_db, db_engine
) -> None:
    await _seed(client_with_db, db_engine)
    resp = await client_with_db.get("/api/v1/missions?language=python")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert ids == {"overfitted-test-fix"}
    assert rows[0]["language"] == "python"
    assert rows[0]["repo_pack_id"] == "data-api-demo"


@pytest.mark.asyncio
async def test_tags_filter_finds_failure_mode(client_with_db, db_engine) -> None:
    await _seed(client_with_db, db_engine)
    resp = await client_with_db.get(
        "/api/v1/missions?tags=checks_presence_not_expiration"
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert ids == {"auth-cookie-expiration"}


@pytest.mark.asyncio
async def test_repo_pack_filter(client_with_db, db_engine) -> None:
    await _seed(client_with_db, db_engine)
    resp = await client_with_db.get(
        "/api/v1/missions?repo_pack=fullstack-auth-demo"
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert ids == {"auth-cookie-expiration"}


@pytest.mark.asyncio
async def test_filters_are_and_combined(client_with_db, db_engine) -> None:
    """Language=go AND repo_pack=fullstack returns the empty set."""
    await _seed(client_with_db, db_engine)
    resp = await client_with_db.get(
        "/api/v1/missions?language=go&repo_pack=fullstack-auth-demo"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_include_upcoming_appends_roadmap_entries(
    client_with_db, db_engine
) -> None:
    await _seed(client_with_db, db_engine)
    resp = await client_with_db.get("/api/v1/missions?include=upcoming")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    statuses = {r["status"] for r in rows}
    assert "shipped" in statuses
    assert "coming_soon" in statuses

    coming = [r for r in rows if r["status"] == "coming_soon"]
    assert coming, "expected at least one coming_soon entry from roadmap.yaml"
    for row in coming:
        assert row["target_release_date"], (
            "coming_soon entries must surface a target_release_date"
        )
        assert row["language"] in {"typescript", "python", "go"}
        assert row["repo_pack_id"] is None
        assert row["tags"] == []


@pytest.mark.asyncio
async def test_include_upcoming_respects_language_filter(
    client_with_db, db_engine
) -> None:
    """Roadmap entries honour the ``?language=`` narrowing."""
    await _seed(client_with_db, db_engine)
    resp = await client_with_db.get(
        "/api/v1/missions?include=upcoming&language=go"
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    # No shipped Go missions yet — only the roadmap entries.
    assert all(r["status"] == "coming_soon" for r in rows)
    assert all(r["language"] == "go" for r in rows)
    assert len(rows) >= 1, "roadmap.yaml ships at least one Go placeholder"


@pytest.mark.asyncio
async def test_language_filter_rejects_unknown_value_with_422(
    client_with_db, db_engine
) -> None:
    """Unknown ``language=`` values 422 at the edge instead of silently emptying the list.

    Prior to the Literal narrowing, ``?language=rust`` would return ``[]``
    because no rows matched — making the filter look "broken" and forcing
    the FE filter dropdown to grow without any backend signal. The Literal
    forces 422 so client errors surface immediately.
    """
    await _seed(client_with_db, db_engine)
    resp = await client_with_db.get("/api/v1/missions?language=rust")
    assert resp.status_code == 422, resp.text
