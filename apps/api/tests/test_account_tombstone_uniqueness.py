"""Gap 3 — two users with a colliding short UUID prefix both tombstone.

The earlier implementation minted tombstone emails / handles from
``user_id.hex[:8]`` — an 8-char prefix has a birthday-paradox collision
around ~65k accounts. When the worker hit a collision, the UNIQUE
constraint on ``users.email`` / ``users.handle`` raised IntegrityError,
the outer try/except rolled the transaction back, and the user was never
deleted (silent indefinite retry loop on every subsequent worker pass).

The fix uses the full 32-char UUID hex; the regression test forces a
collision in the first 8 chars and asserts both users tombstone cleanly
in the same worker pass (no IntegrityError, distinct tombstone emails).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.user import User


async def _bound(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


def _mint_uuid_with_prefix(prefix_hex: str) -> uuid.UUID:
    """Return a random UUID whose hex starts with the given (even-length) prefix.

    UUID v4 has a few fixed bits in the variant + version positions; we
    sidestep that by building the UUID from raw bytes with the prefix
    glued onto a fresh random suffix. The result is NOT a valid v4 (the
    version nibble is overridden by the prefix when prefix_hex covers it)
    but ``uuid.UUID(hex=…)`` accepts any 32 hex chars — and the worker
    code only ever uses ``user_id.hex``, never the version field.
    """
    if len(prefix_hex) > 32 or len(prefix_hex) % 2 != 0:
        raise ValueError("prefix_hex must be an even number of hex chars, <= 32")
    suffix = uuid.uuid4().hex[len(prefix_hex) :]
    return uuid.UUID(hex=prefix_hex + suffix)


@pytest.mark.asyncio
async def test_two_users_with_colliding_8char_prefix_both_tombstone(
    db_engine,
) -> None:
    session_local = await _bound(db_engine)

    # Force a deliberate collision on the *first 8 hex chars* — the
    # historical tombstone scheme would have produced identical
    # ``deleted-{short}@…`` emails for both users and IntegrityError-ed
    # the second one. The fix uses the full 32 hex chars; this test
    # locks the fix in.
    shared_prefix = "deadbeef"
    user_a_id = _mint_uuid_with_prefix(shared_prefix)
    user_b_id = _mint_uuid_with_prefix(shared_prefix)
    assert user_a_id != user_b_id, "fixture must produce two distinct UUIDs"
    assert user_a_id.hex[:8] == user_b_id.hex[:8] == shared_prefix, (
        "fixture must produce a collision in the first 8 hex chars"
    )

    expired = datetime.now(UTC) - timedelta(hours=1)
    async with session_local() as db:
        db.add(
            User(
                id=user_a_id,
                email=f"a-{user_a_id.hex[:8]}@example.com",
                handle=f"a-{user_a_id.hex[:12]}",
                session_epoch=1,
                deletion_scheduled_at=expired,
            )
        )
        db.add(
            User(
                id=user_b_id,
                email=f"b-{user_b_id.hex[:8]}@example.com",
                handle=f"b-{user_b_id.hex[:12]}",
                session_epoch=1,
                deletion_scheduled_at=expired,
            )
        )
        await db.commit()

    # Point the worker at the test SQLite engine.
    from app.db import session as session_module

    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = session_local  # type: ignore[assignment]
    try:
        from app.workers.account_deletion import _async_process_deletion_grace

        processed = await _async_process_deletion_grace()
    finally:
        session_module.AsyncSessionLocal = original  # type: ignore[assignment]

    assert processed == 2, (
        "both colliding users must be tombstoned in the same pass — "
        "the short-prefix scheme would have failed the second one with "
        "IntegrityError on users.email UNIQUE"
    )

    async with session_local() as db:
        rows = (
            (await db.execute(select(User).where(User.id.in_([user_a_id, user_b_id]))))
            .scalars()
            .all()
        )
    assert len(rows) == 2, "tombstones must remain as rows (not hard-deleted)"

    by_id = {row.id: row for row in rows}
    a_row = by_id[user_a_id]
    b_row = by_id[user_b_id]

    # Each row should be tombstoned with the FULL 32-char hex (not a
    # prefix) so the unique constraint is satisfied.
    assert a_row.email == f"deleted-{user_a_id.hex}@deleted.openagentdojo.app"
    assert b_row.email == f"deleted-{user_b_id.hex}@deleted.openagentdojo.app"
    assert a_row.handle == f"deleted-{user_a_id.hex}"
    assert b_row.handle == f"deleted-{user_b_id.hex}"

    # And the two tombstones MUST differ — otherwise the UNIQUE constraint
    # would have rejected the second write.
    assert a_row.email != b_row.email
    assert a_row.handle != b_row.handle

    # Deletion flag cleared, session epoch bumped — same invariants the
    # existing single-user tombstone test pins.
    assert a_row.deletion_scheduled_at is None
    assert b_row.deletion_scheduled_at is None
    assert a_row.session_epoch == 2
    assert b_row.session_epoch == 2
