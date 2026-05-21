"""Mission catalog REST endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.missions.loader import LoadedMission, MissionLoader
from app.missions.service import get_mission, list_published_missions
from app.schemas.mission import MissionDetail, MissionListItem

router = APIRouter(prefix="/missions", tags=["missions"])

# --- in-process manifest cache --------------------------------------------------
#
# The mission tree is read-only at runtime (content updates trigger a redeploy
# / loader re-run), so scanning the YAML once per request is wasted work and
# also drives a classic N+1 on the catalog endpoint.  We cache the loader's
# parsed result keyed by ``(missions_root, manifest_sha_aggregate)`` so any
# content change invalidates the cache automatically without a server restart.

_MANIFEST_CACHE: dict[tuple[str, str], dict[str, LoadedMission]] = {}


def _cached_manifests() -> dict[str, LoadedMission]:
    """Return ``{mission_id: LoadedMission}`` for the configured missions root."""
    settings = get_settings()
    root = str(settings.missions_root)
    loader = MissionLoader(settings.missions_root)
    try:
        loaded = loader.scan()
    except Exception as exc:
        logger.debug("manifest scan failed for {}: {}", root, exc)
        return {}

    # Aggregate sha — cheap and stable; any content change moves it.
    sha = "".join(m.manifest_sha256 for m in loaded)
    key = (root, sha)
    cached = _MANIFEST_CACHE.get(key)
    if cached is None:
        cached = {m.manifest.id: m for m in loaded}
        # Drop any stale entries for the same root with a different sha.
        for k in [k for k in _MANIFEST_CACHE if k[0] == root]:
            _MANIFEST_CACHE.pop(k, None)
        _MANIFEST_CACHE[key] = cached
    return cached


def _detail_extras_for(loaded: LoadedMission | None) -> dict[str, Any]:
    if loaded is None:
        return {
            "brief": "",
            "short_description": "",
            "language_runtime": None,
            "visible_tests": [],
            "expected_context_required": [],
            "expected_context_recommended": [],
            "expected_diff_lines_p50": None,
        }
    m = loaded.manifest
    return {
        "brief": m.brief,
        "short_description": m.short_description,
        "language_runtime": m.repo.language_runtime,
        "visible_tests": list(m.visible_tests),
        "expected_context_required": list(m.expected_context.required),
        "expected_context_recommended": list(m.expected_context.recommended),
        "expected_diff_lines_p50": m.expected_diff_lines_p50,
    }


@router.get("", response_model=list[MissionListItem], summary="List published missions")
async def list_missions(db: AsyncSession = Depends(get_db)) -> list[MissionListItem]:
    rows = await list_published_missions(db)
    manifests = _cached_manifests()
    out: list[MissionListItem] = []
    for r in rows:
        item = MissionListItem.model_validate(r).model_dump()
        loaded = manifests.get(r.id)
        item["short_description"] = loaded.manifest.short_description if loaded else ""
        out.append(MissionListItem.model_validate(item))
    return out


@router.get("/{mission_id}", response_model=MissionDetail, summary="Mission detail")
async def get_mission_detail(mission_id: str, db: AsyncSession = Depends(get_db)) -> MissionDetail:
    row = await get_mission(db, mission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="mission not found")
    base = MissionDetail.model_validate(row).model_dump()
    base.update(_detail_extras_for(_cached_manifests().get(mission_id)))
    return MissionDetail.model_validate(base)


__all__ = ["router"]
