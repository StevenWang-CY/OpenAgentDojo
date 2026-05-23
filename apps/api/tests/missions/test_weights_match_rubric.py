"""The manifest's ``SCORING_WEIGHTS_CANONICAL`` MUST match the grader's
``RUBRIC_DIMENSIONS``. Drift between the two means every mission YAML
validates against weights the grader doesn't honour — the historical
bug had ``verification: 20 / diff_minimality: 5`` on the manifest side
and ``verification: 15 / diff_minimality: 10`` on the grader side, so
every author-declared weight was a lie.
"""
from __future__ import annotations

from app.grading.dimensions import DIMENSION_MAX, RUBRIC_TOTAL
from app.missions.manifest import SCORING_WEIGHTS_CANONICAL


def test_manifest_weights_match_grader_rubric() -> None:
    assert dict(SCORING_WEIGHTS_CANONICAL) == dict(DIMENSION_MAX), (
        "Manifest scoring weights have drifted from the grading rubric. "
        "Update apps/api/app/missions/manifest.py to import from "
        "app.grading.dimensions.DIMENSION_MAX (no duplicated constants)."
    )


def test_manifest_weights_sum_to_rubric_total() -> None:
    assert sum(SCORING_WEIGHTS_CANONICAL.values()) == RUBRIC_TOTAL == 100
