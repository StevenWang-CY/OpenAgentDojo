"""Public verify endpoint contract (P0-11)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.reports.verification import (
    build_envelope,
    compute_hash,
    compute_signature,
    verify_secret,
)


async def _seed(
    db_engine,
    *,
    mission_id: str = "auth-cookie-expiration",
    status: str = "graded",
    mission_kind: str = "standard",
    stamp: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    owner_id = uuid.uuid4()
    session_id = uuid.uuid4()
    submission_id = uuid.uuid4()

    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=owner_id, email="owner@arena.local", display_name="Owner", handle="owner"))
        db.add(
            Mission(
                id=mission_id,
                title="Expired Session Cookie Still Grants Access",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="cookie expiry not enforced",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                kind=mission_kind,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=owner_id,
                mission_id=mission_id,
                status=status,
                score=85,
                attempt_index=1,
            )
        )
        await db.flush()

        sub = Submission(
            id=submission_id,
            session_id=session_id,
            final_diff="x",
            visible_test_results=[],
            hidden_test_results=[],
            validator_results=[],
            score_report={"total": 85, "effective_max": 100, "missed_failure_mode": False},
            total_score=85,
            score_cap_reason=None,
        )
        db.add(sub)
        await db.flush()

        if stamp:
            from datetime import UTC, datetime

            from app.config import get_settings

            secret = verify_secret(get_settings())

            class _S:
                id = submission_id
                total_score = 85
                score_cap_reason = None
                score_report = sub.score_report
                created_at = datetime.now(UTC)

            envelope = build_envelope(
                submission=_S(),
                session=SessionRow(
                    id=session_id,
                    user_id=owner_id,
                    mission_id=mission_id,
                    status="graded",
                    attempt_index=1,
                ),
                manifest=None,
                user=User(id=owner_id, handle="owner", display_name="Owner"),
            )
            h = compute_hash(envelope)
            sub.verification_hash = h
            sub.verification_signature = compute_signature(h, secret)

        await db.commit()

    return owner_id, submission_id


@pytest.mark.asyncio
async def test_verify_endpoint_is_public(client, db_engine) -> None:
    _owner, submission_id = await _seed(db_engine)
    # No cookie, no auth — must still 200.
    resp = await client.get(f"/api/v1/verify/{submission_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["submission_id"] == str(submission_id)
    assert body["total_score"] == 85
    assert body["rubric_version"] == "v1"
    assert body["canonical_url"].endswith(f"/verify/{submission_id}")
    assert body["verification_hash"]
    assert body["verification_signature"]
    assert len(body["verification_hash"]) == 64
    assert len(body["verification_signature"]) == 64
    # Cacheable + indexable headers per design.
    assert "max-age" in resp.headers.get("cache-control", "").lower()
    assert "index" in resp.headers.get("x-robots-tag", "").lower()


@pytest.mark.asyncio
async def test_verify_endpoint_404s_on_non_graded(client, db_engine) -> None:
    _owner, submission_id = await _seed(db_engine, status="submitting")
    resp = await client.get(f"/api/v1/verify/{submission_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_endpoint_404s_on_tutorial(client, db_engine) -> None:
    _owner, submission_id = await _seed(db_engine, mission_kind="tutorial")
    resp = await client.get(f"/api/v1/verify/{submission_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_endpoint_404s_on_missing_hash(client, db_engine) -> None:
    _owner, submission_id = await _seed(db_engine, stamp=False)
    resp = await client.get(f"/api/v1/verify/{submission_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_endpoint_404s_on_unknown_id(client, db_engine) -> None:
    # Seed a different submission to ensure the DB has tables.
    await _seed(db_engine)
    resp = await client.get(f"/api/v1/verify/{uuid.uuid4()}")
    assert resp.status_code == 404
