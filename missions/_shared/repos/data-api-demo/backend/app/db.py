"""Async SQLAlchemy engine + ORM models.

Backed by SQLite (aiosqlite) for the demo so the test suite is hermetic.
Production would point ``DATABASE_URL`` at Postgres + asyncpg; the rest of
the app does not care which dialect is in play.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _default_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


class Base(DeclarativeBase):
    """Project declarative base."""


class Job(Base):
    """A queue item processed by ``app.jobs.process_job``.

    States:

      * ``pending``  — newly enqueued, available to a worker
      * ``running``  — claimed by a worker; only one worker should ever
        observe this transition for any given job
      * ``done``     — successfully processed
      * ``failed``   — processor raised
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payload: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


def make_engine(url: str | None = None) -> AsyncEngine:
    """Build an async engine. Each call yields a fresh engine."""
    return create_async_engine(url or _default_url(), future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_schema(engine: AsyncEngine) -> None:
    """Create all tables. Idempotent — safe to call on every boot."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Async context-manager generator for one transactional session."""
    async with session_factory() as session:
        yield session
