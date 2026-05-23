"""Diff minimality refuses to honour an out-of-band p50 (P1-3).

An author-declared ``expected_diff_lines_p50`` of 1 would silently
mark every realistic fix as 0/10 on diff_minimality; a value of 1000
would let a runaway rewrite score 10/10. Both shift the entire
mission's scoring band invisibly. The score engine clamps to the
plausibility band [3, 200] and falls back to 20 otherwise — and
``validate_missions.py`` is the primary gate that rejects such
manifests at content-author time.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.grading.diff import ParsedDiff
from app.grading.score import _score_diff_minimality


@dataclass
class _Manifest:
    id: str = "p50-plausibility-test"
    expected_diff_lines_p50: int = 20


def _diff_with_n_added_lines(n: int) -> ParsedDiff:
    body = ["--- a/src/a.ts", "+++ b/src/a.ts", f"@@ -1,1 +1,{n + 1} @@", " const x = 1;"]
    body.extend([f"+const y{i} = {i};" for i in range(n)])
    return ParsedDiff("\n".join(body) + "\n")


def test_in_band_p50_is_honoured() -> None:
    """A reasonable p50 (e.g. 20) is used as-is, no fallback warning."""
    manifest = _Manifest(expected_diff_lines_p50=20)
    diff = _diff_with_n_added_lines(15)  # ratio = 15/20 = 0.75 → 10/10.
    ds = _score_diff_minimality(diff, manifest)
    assert ds.score == 10
    assert not any("out of band" in s for s in ds.signals)


def test_p50_too_small_falls_back_to_20() -> None:
    manifest = _Manifest(expected_diff_lines_p50=1)
    diff = _diff_with_n_added_lines(15)
    ds = _score_diff_minimality(diff, manifest)
    # Without the clamp, ratio = 15/1 = 15 → 0/10.
    # With the clamp, fallback p50 = 20 → ratio 0.75 → 10/10.
    assert ds.score == 10
    assert any("out of band" in s for s in ds.signals)


def test_p50_too_large_falls_back_to_20() -> None:
    manifest = _Manifest(expected_diff_lines_p50=1000)
    diff = _diff_with_n_added_lines(80)
    ds = _score_diff_minimality(diff, manifest)
    # Without the clamp, ratio = 80/1000 = 0.08 → 10/10 (runaway).
    # With the clamp, fallback p50 = 20 → ratio = 4 → 0/10.
    assert ds.score == 0
    assert any("out of band" in s for s in ds.signals)


def test_p50_at_lower_boundary_is_in_band() -> None:
    """p50=3 is the smallest realistic mission (one-line fix)."""
    manifest = _Manifest(expected_diff_lines_p50=3)
    diff = _diff_with_n_added_lines(3)  # ratio = 1.0 → 10/10.
    ds = _score_diff_minimality(diff, manifest)
    assert ds.score == 10
    assert not any("out of band" in s for s in ds.signals)


def test_p50_at_upper_boundary_is_in_band() -> None:
    manifest = _Manifest(expected_diff_lines_p50=200)
    diff = _diff_with_n_added_lines(200)
    ds = _score_diff_minimality(diff, manifest)
    assert ds.score == 10
    assert not any("out of band" in s for s in ds.signals)


def test_non_int_p50_falls_back_to_20() -> None:
    manifest = _Manifest(expected_diff_lines_p50="twenty")  # type: ignore[arg-type]
    diff = _diff_with_n_added_lines(15)
    ds = _score_diff_minimality(diff, manifest)
    assert ds.score == 10
    assert any("out of band" in s for s in ds.signals)
