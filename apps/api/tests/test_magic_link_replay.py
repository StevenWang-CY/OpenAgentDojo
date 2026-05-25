"""Replaying an already-used magic-link token must be rejected AND
logged as a warning so incident response has a thread to pull. Previously
``consume_magic_token`` returned ``None`` for both unknown and
already-used tokens with no observable difference.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from loguru import logger

from app.auth.magic_link import _hash_token, consume_magic_token
from app.models.magic_link_token import MagicLinkToken
from app.models.user import User


@pytest.mark.asyncio
async def test_replay_of_used_token_logs_warning(db_session) -> None:  # type: ignore[no-untyped-def]
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(m.record["message"]), level="WARNING")
    try:
        user = User(email="replay-test@example.com")
        db_session.add(user)
        await db_session.flush()

        raw_token = "fake-token-deadbeef"
        token = MagicLinkToken(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            used_at=datetime.now(UTC),  # already consumed
        )
        db_session.add(token)
        await db_session.flush()

        result = await consume_magic_token(db_session, raw_token)
        assert result is None

        # Allow the loguru sink to flush.
        await asyncio.sleep(0.01)
        assert any("magic_link.replay" in m for m in captured), captured
    finally:
        logger.remove(sink_id)


@pytest.mark.asyncio
async def test_unknown_token_returns_none_silently(db_session) -> None:  # type: ignore[no-untyped-def]
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(m.record["message"]), level="WARNING")
    try:
        result = await consume_magic_token(db_session, "never-issued-token")
        assert result is None
        await asyncio.sleep(0.01)
        # Unknown tokens are not necessarily an attack — most are users
        # clicking expired email links. Don't spam WARNING for them.
        assert not any("magic_link" in m for m in captured), captured
    finally:
        logger.remove(sink_id)
