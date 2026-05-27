"""Mission catalog REST endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.session import get_db
from app.missions.cache import cached_manifests, detail_extras_for
from app.missions.roadmap import filter_collisions, load_roadmap
from app.missions.service import get_mission, list_published_missions
from app.missions.your_attempts import load_your_attempts
from app.models.repo_pack import RepoPack
from app.models.user import User
from app.schemas.mission import MissionDetail, MissionListItem

router = APIRouter(prefix="/missions", tags=["missions"])


async def _pack_language_map(db: AsyncSession) -> dict[str, str]:
    """Return ``{repo_pack_id: language}`` for every seeded pack.

    The mission list endpoint joins each row's ``repo_pack_id`` against
    this map so the FE language chip and the ``?language=`` filter share
    one source of truth (the ``repo_packs`` table). Cold-cache cost is
    one ``SELECT id, language FROM repo_packs`` per request; the table
    has ≤ 5 rows in steady state.
    """
    rows = (await db.execute(select(RepoPack.id, RepoPack.language))).all()
    return {row[0]: row[1] for row in rows}


@router.get("", response_model=list[MissionListItem], summary="List published missions")
async def list_missions(
    db: AsyncSession = Depends(get_db),
    tags: list[str] | None = Query(
        default=None,
        description=(
            "Filter to missions whose ``tags`` contain *any* of the listed values "
            "(closed vocabulary; see apps/api/app/missions/manifest.py::_KNOWN_TAGS)."
        ),
    ),
    repo_pack: str | None = Query(
        default=None,
        description="Filter to missions whose ``repo_pack_id`` equals this value.",
    ),
    language: Literal["typescript", "python", "go"] | None = Query(
        default=None,
        description=(
            "Filter to missions whose repo pack has ``repo_packs.language == language``. "
            "Closed vocabulary — unknown values 422 at the edge instead of silently "
            "returning an empty list."
        ),
    ),
    include: Literal["upcoming"] | None = Query(
        default=None,
        description=(
            "When set to ``upcoming``, append dated ``coming_soon`` entries "
            "from ``apps/api/app/missions/roadmap.yaml`` to the response."
        ),
    ),
) -> list[MissionListItem]:
    """List published missions with optional catalog filters (P1-1).

    Filter semantics
    ----------------
    * ``tags`` is an OR-within-the-param, AND-across-params filter — a row
      matches if it carries *any* of the supplied tags. Multiple ``tags``
      query params chain via the usual FastAPI list collation.
    * ``repo_pack`` and ``language`` are equality matches against the
      mission's pack metadata.
    * Coming-soon entries are independent of the ``tags`` / ``repo_pack``
      filters (they have no manifest yet); ``language`` *does* apply to
      placeholders since the roadmap entry carries its own language.
    """
    rows = await list_published_missions(db)
    manifests = cached_manifests()
    pack_lang = await _pack_language_map(db)

    out: list[MissionListItem] = []
    selected_tags = set(tags or [])
    for r in rows:
        row_tags: list[str] = list(getattr(r, "tags", []) or [])
        row_pack_id = getattr(r, "repo_pack_id", None) or r.repo_pack
        row_language = pack_lang.get(row_pack_id, "typescript")

        if selected_tags and not selected_tags.intersection(row_tags):
            continue
        if repo_pack is not None and row_pack_id != repo_pack:
            continue
        if language is not None and row_language != language:
            continue

        item = MissionListItem.model_validate(r).model_dump()
        loaded = manifests.get(r.id)
        item["short_description"] = loaded.manifest.short_description if loaded else ""
        item["tags"] = row_tags
        item["repo_pack_id"] = row_pack_id
        item["language"] = row_language
        item["status"] = "shipped"
        item["target_release_date"] = None
        out.append(MissionListItem.model_validate(item))

    if include == "upcoming":
        # Drop any placeholder whose id collides with a shipped mission —
        # the author-time invariant is enforced by review, but a drift would
        # otherwise surface as two cards with the same id in the catalog.
        # ``rows`` is already loaded above; reuse it to avoid a second query.
        shipped_ids = {row.id for row in rows}
        roadmap = filter_collisions(load_roadmap(), shipped_ids)
        for placeholder in roadmap.placeholders:
            if selected_tags:
                # Roadmap entries have no tags yet; honour the AND-rule by
                # skipping them when the caller explicitly narrowed by tag.
                continue
            if repo_pack is not None:
                # No pack assigned; skip when the caller asked for one.
                continue
            if language is not None and placeholder.language != language:
                continue
            out.append(
                MissionListItem(
                    id=placeholder.id,
                    title=placeholder.title,
                    short_description=placeholder.teaser,
                    failure_mode_id="",
                    skills_tested=[],
                    tags=[],
                    repo_pack_id=None,
                    language=placeholder.language,
                    status="coming_soon",
                    target_release_date=placeholder.target_release_date.isoformat(),
                    kind="standard",
                    estimated_minutes=0,
                    version=1,
                    # ``status`` is the canonical FE discriminator — keep
                    # ``published=True`` so coming-soon entries flow through
                    # the same "publicly readable" gate as shipped rows. The
                    # FE branches on ``status`` to render the muted card +
                    # dated chip; ``published`` stays a private/draft signal.
                    published=True,
                )
            )
    return out


@router.get(
    "/{mission_id}",
    response_model=MissionDetail,
    summary="Mission detail",
    responses={
        404: {
            "description": (
                "No mission with the given id is published (or the id does "
                "not exist). Anonymous and signed-in callers see the same "
                "shape — the field surface never hints at whether the row "
                "exists but is unpublished."
            ),
        },
    },
)
async def get_mission_detail(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    viewer: User | None = Depends(get_current_user),
) -> MissionDetail:
    row = await get_mission(db, mission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="mission not found")
    base: dict[str, Any] = MissionDetail.model_validate(row).model_dump()
    base.update(detail_extras_for(cached_manifests().get(mission_id)))

    # P1-1 — surface the catalog-level fields on the detail payload so the
    # FE workspace can render the language chip + tag list without a second
    # roundtrip through the list endpoint. ``language`` is resolved through
    # the same ``repo_packs`` join the catalog list uses (single source of
    # truth); ``tags`` comes from the mission row directly.
    pack_lang = await _pack_language_map(db)
    row_pack_id = getattr(row, "repo_pack_id", None) or row.repo_pack
    base["repo_pack_id"] = row_pack_id
    base["language"] = pack_lang.get(row_pack_id, "typescript")
    base["tags"] = list(getattr(row, "tags", []) or [])
    # Shipped detail responses ALWAYS carry ``status='shipped'``; the
    # coming-soon detail surface is not yet exposed (placeholders live
    # on the list endpoint via ``?include=upcoming``).
    base["status"] = "shipped"
    base["target_release_date"] = None

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
