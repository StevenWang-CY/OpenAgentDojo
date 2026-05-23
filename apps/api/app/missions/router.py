"""Mission catalog REST endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.session import get_db
from app.missions.cache import cached_manifests, detail_extras_for
from app.missions.service import get_mission, list_published_missions
from app.missions.your_attempts import load_your_attempts
from app.models.user import User
from app.schemas.mission import MissionDetail, MissionListItem

router = APIRouter(prefix="/missions", tags=["missions"])


@router.get("", response_model=list[MissionListItem], summary="List published missions")
async def list_missions(db: AsyncSession = Depends(get_db)) -> list[MissionListItem]:
    rows = await list_published_missions(db)
    manifests = cached_manifests()
    out: list[MissionListItem] = []
    for r in rows:
        item = MissionListItem.model_validate(r).model_dump()
        loaded = manifests.get(r.id)
        item["short_description"] = loaded.manifest.short_description if loaded else ""
        out.append(MissionListItem.model_validate(item))
    return out


@router.get("/{mission_id}", response_model=MissionDetail, summary="Mission detail")
async def get_mission_detail(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    viewer: User | None = Depends(get_current_user),
) -> MissionDetail:
    row = await get_mission(db, mission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="mission not found")
    base = MissionDetail.model_validate(row).model_dump()
    base.update(detail_extras_for(cached_manifests().get(mission_id)))
    # P0-3 — when the caller is signed in, attach their per-mission attempt
    # summary so the FE can render the "// your attempts" strip without a
    # second roundtrip. Anonymous viewers get ``None`` (the FE branches on
    # this to render the standard catalog CTA).
    if viewer is not None:
        base["your_attempts"] = (
            await load_your_attempts(db, user_id=viewer.id, mission_id=mission_id)
        ).model_dump()
    return MissionDetail.model_validate(base)


__all__ = ["router"]
