"""Single source of truth for the seven rubric dimensions and their max scores."""
from __future__ import annotations

from typing import Final

RUBRIC_DIMENSIONS: Final[tuple[tuple[str, int], ...]] = (
    ("final_correctness", 30),
    ("verification", 15),
    ("agent_review", 15),
    ("prompt_quality", 10),
    ("context_selection", 10),
    ("safety", 10),
    ("diff_minimality", 10),
)

DIMENSION_NAMES: Final[tuple[str, ...]] = tuple(name for name, _ in RUBRIC_DIMENSIONS)
DIMENSION_MAX: Final[dict[str, int]] = dict(RUBRIC_DIMENSIONS)
RUBRIC_TOTAL: Final[int] = sum(DIMENSION_MAX.values())
