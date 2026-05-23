"""Every mission id recommended by the diagnostic narrative must exist
in the on-disk mission catalog. Otherwise a rename or removal will
silently emit dead links in the feedback narrative.
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.grading.diagnostics import _RECOMMENDED_BY_DIMENSION
from app.missions.loader import MissionLoader


def test_recommended_mission_ids_exist_in_catalog() -> None:
    get_settings.cache_clear()
    root: Path = get_settings().missions_root
    catalog = {m.manifest.id for m in MissionLoader(root).scan()}
    missing: list[tuple[str, str]] = []
    for dim, mission_ids in _RECOMMENDED_BY_DIMENSION.items():
        for mid in mission_ids:
            if mid not in catalog:
                missing.append((dim, mid))
    assert not missing, (
        "diagnostics recommends mission id(s) that don't exist on disk:\n"
        + "\n".join(f"  {dim} → {mid!r}" for dim, mid in missing)
    )
