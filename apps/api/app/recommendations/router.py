"""``GET /api/v1/me/recommendations`` (P1-2).

The recommendation set is per-user, so the endpoint requires auth and
401s for anonymous callers via :func:`app.auth.deps.require_auth`. The
hot path is a single SELECT against ``user_recommendations`` (TTL +
``invalidated_at`` gated) followed by 1-2 cheap joined SELECTs for the
mission shape; cold path recomputes the deterministic ranking through
:func:`app.recommendations.cache.get_cached_or_compute`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_auth
from app.db.session import get_db
from app.models.user import User
from app.recommendations.cache import get_cached_or_compute
from app.recommendations.schemas import RecommendationSet

router = APIRouter(prefix="/me", tags=["recommendations"])


@router.get(
    "/recommendations",
    response_model=RecommendationSet,
    summary="Adaptive next-mission recommendation for the signed-in user",
    responses={
        401: {
            "description": (
                "Authentication required — the recommendation set is "
                "per-user. Sign in via the magic-link flow or GitHub OAuth "
                "and retry."
            ),
        },
    },
)
async def get_recommendations(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> RecommendationSet:
    """Return the top-3 next missions tailored to ``user``.

    Always returns a populated :class:`RecommendationSet`. Cold-start
    users (no graded submissions) get the introductory ladder; users
    who have graded every shipped mission get the largest-gap retry +
    two ``coming_soon`` placeholders. The endpoint never 5xx's on a
    missing-cache row — the engine recomputes deterministically and
    persists the result.
    """
    return await get_cached_or_compute(db, user.id)


__all__ = ["router"]
