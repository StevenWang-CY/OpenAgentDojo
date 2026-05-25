"""Magic-link signup populates ``user.handle`` and resolves collisions."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.auth.magic_link import _slugify_handle, create_magic_link
from app.models.user import User


def test_slugify_strips_non_alphanumeric() -> None:
    assert _slugify_handle("Alice.42+test@example.com") == "alice42test"
    assert _slugify_handle("UPPER@x.com") == "upper"
    # All-symbol local-part falls back to "user".
    assert _slugify_handle("____@x.com") == "user"


@pytest.mark.asyncio
async def test_signup_creates_user_with_handle(db_session) -> None:
    await create_magic_link(
        db_session,
        email="newperson@example.com",
        base_url="http://api.local",
    )
    await db_session.commit()

    row = (
        await db_session.execute(select(User).where(User.email == "newperson@example.com"))
    ).scalar_one()
    assert row.handle == "newperson"


@pytest.mark.asyncio
async def test_handle_collision_appends_numeric_suffix(db_session) -> None:
    """Two users whose local-parts slugify to the same handle get -2, -3."""
    # Three users, all of which slugify to "collide".
    await create_magic_link(db_session, email="collide@a.com", base_url="http://api.local")
    await db_session.commit()
    await create_magic_link(db_session, email="Collide@b.com", base_url="http://api.local")
    await db_session.commit()
    await create_magic_link(db_session, email="c.o.l.l.i.d.e@c.com", base_url="http://api.local")
    await db_session.commit()

    # Phase 4.A.12 — ``create_magic_link`` normalises ``email`` to
    # ``email.strip().lower()`` at the route boundary, so the SQLite
    # test path (which stores TEXT, not CITEXT) sees the lowercased
    # form regardless of the original casing.
    handles = {
        row.handle
        for row in (
            await db_session.execute(
                select(User).where(
                    User.email.in_(["collide@a.com", "collide@b.com", "c.o.l.l.i.d.e@c.com"])
                )
            )
        )
        .scalars()
        .all()
    }
    assert handles == {"collide", "collide-2", "collide-3"}


@pytest.mark.asyncio
async def test_repeat_signup_for_existing_user_does_not_change_handle(db_session) -> None:
    """If a user re-requests a magic link their handle is preserved."""
    await create_magic_link(db_session, email="stable@example.com", base_url="http://api.local")
    await db_session.commit()
    row1 = (
        await db_session.execute(select(User).where(User.email == "stable@example.com"))
    ).scalar_one()
    original_handle = row1.handle

    await create_magic_link(db_session, email="stable@example.com", base_url="http://api.local")
    await db_session.commit()
    row2 = (
        await db_session.execute(select(User).where(User.email == "stable@example.com"))
    ).scalar_one()
    assert row2.handle == original_handle
