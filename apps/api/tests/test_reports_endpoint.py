"""Reports REST endpoint tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User


async def _setup(db_engine, mission_id="auth-cookie-expiration"):
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    owner_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    session_id = uuid.uuid4()
    submission_id = uuid.uuid4()

    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=owner_id, email="owner@arena.local", display_name="O"))
        db.add(
            User(
                id=stranger_id,
                email="stranger@arena.local",
                display_name="S",
            )
        )
        db.add(
            Mission(
                id=mission_id,
                title="x",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="HEAD",
                estimated_minutes=10,
                failure_mode="x",
                skills_tested=["auth"],
                manifest_sha256="sha",
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=owner_id,
                mission_id=mission_id,
                status="graded",
                score=85,
            )
        )
        await db.flush()
        db.add(
            Submission(
                id=submission_id,
                session_id=session_id,
                final_diff="x",
                visible_test_results={},
                hidden_test_results={},
                validator_results={},
                score_report={"total": 85, "dimensions": {}},
                total_score=85,
            )
        )
        await db.commit()

    return owner_id, stranger_id, session_id, submission_id


def _make_session_cookie(user_id: uuid.UUID) -> str:
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


@pytest.mark.asyncio
async def test_owner_can_read_report(client, db_engine) -> None:
    owner_id, _, _, submission_id = await _setup(db_engine)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(
        settings.session_cookie_name, _make_session_cookie(owner_id)
    )
    resp = await client.get(f"/api/v1/reports/{submission_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(submission_id)
    # ideal_solution.md is rendered because status='graded'.
    assert "ideal_solution" in body


@pytest.mark.asyncio
async def test_stranger_gets_403(client, db_engine) -> None:
    _, stranger_id, _, submission_id = await _setup(db_engine)
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(
        settings.session_cookie_name, _make_session_cookie(stranger_id)
    )
    resp = await client.get(f"/api/v1/reports/{submission_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_share_token_grants_access(client, db_engine) -> None:
    _, _, _, submission_id = await _setup(db_engine)
    from app.config import get_settings
    from app.reports.router import issue_share_token

    settings = get_settings()
    token, _ = issue_share_token(submission_id, settings)
    # No session cookie — only the share token.
    client.cookies.clear()
    resp = await client.get(
        f"/api/v1/reports/{submission_id}?share={token}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(submission_id)


@pytest.mark.asyncio
async def test_bad_share_token_rejected(client, db_engine) -> None:
    _, _, _, submission_id = await _setup(db_engine)
    client.cookies.clear()
    resp = await client.get(
        f"/api/v1/reports/{submission_id}?share=this-is-not-a-jwt"
    )
    # The route now returns a structured 400 when a share param is present but
    # cannot be validated (clearer than a generic 401 for someone with a
    # malformed link). The detail carries a ``reason`` so the FE can render an
    # informative "your share link is broken" state.
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["detail"]["reason"] in {"invalid", "expired", "malformed"}
    assert "share link" in body["detail"]["message"].lower()


@pytest.mark.asyncio
async def test_owner_can_mint_share_url(client, db_engine) -> None:
    owner_id, _, _, submission_id = await _setup(db_engine)
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(
        settings.session_cookie_name, _make_session_cookie(owner_id)
    )

    csrf = "y" * 64
    client.cookies.set("arena_csrf", csrf)
    resp = await client.post(
        f"/api/v1/reports/{submission_id}/share",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "share_url" in body
    assert "share_token" in body
    assert "expires_at" in body
    assert str(submission_id) in body["share_url"]
