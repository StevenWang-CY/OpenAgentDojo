"""P1-6 audit item 9 — session_notes body cap counts bytes, not chars.

Pre-audit the CHECK was ``length(body) <= 32768`` which on Postgres
counts characters; a 32 768-glyph multi-byte payload could weigh up to
~128 KB on disk. Migration ``0033_session_notes_byte_check`` swaps the
constraint to ``octet_length(body) <= 32768`` so it matches the
API-layer 413 guard (which counts UTF-8 bytes).

SQLite gets the same CHECK via ``Base.metadata.create_all`` in
``tests/conftest.py``. Verified separately that SQLite's
``octet_length`` is a built-in (CREATE accepts it; oversized INSERTs
fail with ``CHECK constraint failed``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.session_note import SessionNote
from app.models.user import User

_USER_ID = uuid.UUID("b1111111-1111-4111-8111-111111111111")
_SESSION_ID = uuid.UUID("b2222222-2222-4222-8222-222222222222")
_MISSION_ID = "audit-byte-check"


async def _seed_user_mission_session(db_engine) -> None:
    async with async_sessionmaker(bind=db_engine, expire_on_commit=False)() as db:
        db.add(
            User(
                id=_USER_ID,
                email="b@arena.local",
                display_name="B",
                handle="b",
            )
        )
        db.add(
            Mission(
                id=_MISSION_ID,
                title="Byte check fixture",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="abc123de",
                estimated_minutes=10,
                failure_mode="placeholder",
                skills_tested=["auth"],
                manifest_sha256="cafef00d" * 8,
                version=1,
                kind="standard",
                published=True,
                expected_weak_dim="safety",
            )
        )
        db.add(
            SessionRow(
                id=_SESSION_ID,
                user_id=_USER_ID,
                mission_id=_MISSION_ID,
                status="active",
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_session_notes_check_constraint_uses_bytes(db_engine) -> None:
    """The CHECK should reject a body whose UTF-8 byte length exceeds 32 768.

    32 768 single-byte ASCII chars MUST be accepted (boundary), but the
    same byte budget of multi-byte glyphs has fewer characters — and the
    new check must enforce the byte ceiling, not the char ceiling.
    """
    await _seed_user_mission_session(db_engine)

    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    # 32 768 ASCII bytes = boundary. Must be ACCEPTED.
    boundary_body = "a" * 32_768
    async with session_local() as db:
        db.add(
            SessionNote(
                session_id=_SESSION_ID,
                body=boundary_body,
                updated_at=datetime(2026, 5, 28, tzinfo=UTC),
            )
        )
        await db.commit()

    # Replace with 33 single-byte chars over the boundary — must FAIL.
    async with session_local() as db:
        row = await db.get(SessionNote, _SESSION_ID)
        row.body = "a" * 32_800
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()

    # And: a multi-byte (UTF-8) body that exceeds the BYTE budget but is
    # under the CHAR budget must also be rejected. "é" is 2 bytes in
    # UTF-8; 17 000 copies = 34 000 bytes (over) and 17 000 chars
    # (well under the 32 768 cap if we were counting characters). The
    # old character-based check would have let this through.
    async with session_local() as db:
        row = await db.get(SessionNote, _SESSION_ID)
        row.body = "é" * 17_000
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.mark.asyncio
async def test_session_notes_check_constraint_admits_ascii_under_cap(db_engine) -> None:
    """A small ASCII body must round-trip without complaint."""
    await _seed_user_mission_session(db_engine)
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_local() as db:
        db.add(
            SessionNote(
                session_id=_SESSION_ID,
                body="hello, world",
                updated_at=datetime(2026, 5, 28, tzinfo=UTC),
            )
        )
        await db.commit()
    async with session_local() as db:
        row = await db.get(SessionNote, _SESSION_ID)
        assert row is not None
        assert row.body == "hello, world"
