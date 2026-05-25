"""Diff minimality must use ``max(added, removed)`` so destructive minimisation
doesn't earn a perfect score.

A patch that wipes out 200 lines and only adds 5 used to score 10/10
because the rubric ignored deletions. The new rule symmetrically penalises
churn in either direction.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.grading.diff import ParsedDiff
from app.grading.score import _score_diff_minimality


@dataclass
class _Manifest:
    expected_diff_lines_p50: int = 20


def _build_diff(added: int, removed: int) -> str:
    """Build a synthetic diff with exactly ``added`` + and ``removed`` - lines.

    Always sandwiches the +/- lines with one shared context line so the hunk
    counts (source ``removed + 1`` lines, target ``added + 1`` lines) align
    with the @@ header. ``unidiff`` is strict about hunk arithmetic.
    """
    source_len = removed + 1
    target_len = added + 1
    lines = [
        "--- a/foo.ts",
        "+++ b/foo.ts",
        f"@@ -1,{source_len} +1,{target_len} @@",
        " context_line",
    ]
    for i in range(removed):
        lines.append(f"-old_line_{i}")
    for i in range(added):
        lines.append(f"+new_line_{i}")
    return "\n".join(lines) + "\n"


def test_destructive_minimisation_does_not_score_full_credit() -> None:
    """Wiping out 200 lines while adding 5 must not score 10/10."""
    diff = ParsedDiff(_build_diff(added=5, removed=200))
    score = _score_diff_minimality(diff, _Manifest(expected_diff_lines_p50=20))
    # churn = max(5, 200) = 200; ratio = 200/20 = 10 → 0/10.
    assert score.score == 0, (
        f"churn=200 over p50=20 should be 0/10, got {score.score}; signals={score.signals}"
    )


def test_added_only_still_scored_the_same() -> None:
    """If removed <= added, score is identical to the legacy added-only rule."""
    diff = ParsedDiff(_build_diff(added=18, removed=0))
    score = _score_diff_minimality(diff, _Manifest(expected_diff_lines_p50=20))
    # ratio = 18/20 = 0.9 → full credit 10/10.
    assert score.score == 10, (
        f"add-only ratio<1 should be 10/10, got {score.score}; signals={score.signals}"
    )


def test_max_is_10_not_5() -> None:
    """Diff minimality's max scaled up from 5 to 10 in the rubric refresh."""
    diff = ParsedDiff(_build_diff(added=1, removed=0))
    score = _score_diff_minimality(diff, _Manifest())
    assert score.max_score == 10
