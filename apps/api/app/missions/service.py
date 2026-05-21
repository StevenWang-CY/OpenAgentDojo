"""Mission DB queries used by the router."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mission import Mission


async def list_published_missions(db: AsyncSession) -> list[Mission]:
    """Return every published mission ordered by id."""
    stmt = select(Mission).where(Mission.published.is_(True)).order_by(Mission.id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_mission(db: AsyncSession, mission_id: str) -> Mission | None:
    stmt = select(Mission).where(Mission.id == mission_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
