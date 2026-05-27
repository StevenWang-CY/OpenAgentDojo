"""Adaptive next-mission recommendation engine (P1-2).

The module splits cleanly into four files so each concern is independently
testable:

  * ``engine`` — pure ranking function: same ``(user_history, mission_catalogue,
    RUBRIC_VERSION)`` → byte-identical output.
  * ``cache``  — read/write helpers against the ``user_recommendations`` table.
  * ``copy``   — deterministic per-dimension diagnosis + per-mission "why"
    strings. The LLM-polish layer (P1-2 PR2) replaces this on the hot path
    once the §0.4 cache lands; until then the deterministic copy is what
    ships.
  * ``schemas`` — Pydantic shapes returned by ``GET /me/recommendations``.

The router is wired in ``app.recommendations.router`` and mounted under
``/api/v1`` in ``app.main.create_app``.
"""

from app.recommendations.engine import (
    FRESH_MISSION_IDS,
    INTRODUCTORY_LADDER,
    PASS_THRESHOLD,
    MissionCandidate,
    UserHistory,
    recommend,
)
from app.recommendations.schemas import RecommendationItem, RecommendationSet

__all__ = [
    "FRESH_MISSION_IDS",
    "INTRODUCTORY_LADDER",
    "PASS_THRESHOLD",
    "MissionCandidate",
    "RecommendationItem",
    "RecommendationSet",
    "UserHistory",
    "recommend",
]
