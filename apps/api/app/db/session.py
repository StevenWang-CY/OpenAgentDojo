"""Async SQLAlchemy session/engine wiring + FastAPI dependency.

Transaction model for the ``get_db`` dependency
------------------------------------------------
The dependency yields a single ``AsyncSession`` per request and commits *once*
when the handler returns successfully. On exception we rollback. After a
successful commit we drain any Redis publishes that the handler queued via
:class:`app.sessions.events.EventEmitter` — this guarantees subscribers never
see an event whose producing transaction later rolled back.

The engine + sessionmaker are constructed lazily so test harnesses can replace
``AsyncSessionLocal`` with one bound to an in-memory SQLite engine after the
module has been imported.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        echo=False,
    )


def _build_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


# Module-level sessionmaker — kept as a mutable attribute (not lru_cached) so
# tests can rebind via ``app.db.session.AsyncSessionLocal = ...`` after a fresh
# engine override. ``get_db`` always reads through this attribute so the
# override is picked up immediately.
AsyncSessionLocal: async_sessionmaker[AsyncSession] = _build_sessionmaker()


def reset_sessionmaker() -> None:
    """Drop the cached engine + rebuild the sessionmaker.

    Test helper for fixtures that change the configured DATABASE_URL between
    tests — call this after mutating settings to ensure a fresh engine is
    constructed on the next request.
    """
    global AsyncSessionLocal  # noqa: PLW0603 — intentional module-level rebind for tests
    get_engine.cache_clear()
    AsyncSessionLocal = _build_sessionmaker()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional async session.

    On success: ``commit()`` then drain any queued Redis publishes.
    On exception: ``rollback()`` and discard the queue so failed events never
    leak to Redis subscribers.
    """
    # Local import — events.py imports config which is fine, but we avoid
    # eager import to keep this module's import cost minimal in tooling that
    # only needs the engine.
    from app.sessions.events import clear_pending_publishes, drain_pending_publishes

    # Read through the module attribute so test rebinds take effect.
    session_local = AsyncSessionLocal
    async with session_local() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            clear_pending_publishes(session)
            raise
        else:
            await session.commit()
            await drain_pending_publishes(session)
