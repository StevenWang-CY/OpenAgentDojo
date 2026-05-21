"""Smoke-test: every model can be inserted and queried via the SQLite engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    AgentTurn,
    Badge,
    CommandRun,
    FileChange,
    MagicLinkToken,
    Mission,
    SessionRow,
    Submission,
    SupervisionEvent,
    User,
    UserBadge,
)


@pytest.mark.asyncio
async def test_insert_and_query_every_model(db_session) -> None:
    user = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        display_name="Alice",
    )
    mission = Mission(
        id="auth-cookie-expiration",
        title="Sample",
        difficulty="intermediate",
        category="auth",
        repo_pack="fullstack-auth-demo",
        initial_commit="abc123de",
        estimated_minutes=30,
        failure_mode="checks_presence_not_expiration",
        skills_tested=["auth"],
        manifest_sha256="a" * 64,
        version=1,
        published=True,
    )
    db_session.add_all([user, mission])
    await db_session.flush()

    session = SessionRow(
        id=uuid.uuid4(),
        user_id=user.id,
        mission_id=mission.id,
        status="provisioning",
    )
    db_session.add(session)
    await db_session.flush()

    db_session.add_all(
        [
            AgentTurn(
                session_id=session.id,
                turn_index=0,
                user_prompt="fix the bug",
                selected_context={"files": ["a.ts"]},
                agent_response="ok",
            ),
            FileChange(
                session_id=session.id,
                path="a.ts",
                source="user",
                hunk_count=1,
                added_lines=1,
                removed_lines=0,
            ),
            CommandRun(
                session_id=session.id,
                command="pnpm test",
                exit_code=0,
                duration_ms=120,
                category="test",
            ),
            SupervisionEvent(
                session_id=session.id,
                event_type="session.started",
                payload={"ok": True},
            ),
            Badge(
                id="test-badge",
                title="Test",
                description="d",
                icon="x",
            ),
        ]
    )
    await db_session.flush()

    db_session.add(UserBadge(user_id=user.id, badge_id="test-badge", session_id=session.id))
    db_session.add(
        Submission(
            session_id=session.id,
            final_diff="diff --git a/a b/a",
            visible_test_results={},
            hidden_test_results={},
            validator_results={},
            score_report={"total": 0},
            total_score=0,
        )
    )
    db_session.add(
        MagicLinkToken(
            user_id=user.id,
            token_hash="x" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
    )
    await db_session.commit()

    assert (await db_session.execute(select(User))).scalar_one()
    assert (await db_session.execute(select(Mission))).scalar_one()
    assert (await db_session.execute(select(SessionRow))).scalar_one()
    assert (await db_session.execute(select(AgentTurn))).scalar_one()
    assert (await db_session.execute(select(FileChange))).scalar_one()
    assert (await db_session.execute(select(CommandRun))).scalar_one()
    assert (await db_session.execute(select(SupervisionEvent))).scalar_one()
    assert (await db_session.execute(select(Badge))).scalar_one()
    assert (await db_session.execute(select(UserBadge))).scalar_one()
    assert (await db_session.execute(select(Submission))).scalar_one()
    assert (await db_session.execute(select(MagicLinkToken))).scalar_one()
