"""``_passed`` uses percent-of-effective-max, not raw integer 70.

The drift fix replaces the raw ``score >= 70`` test with
``score >= max(1, int(effective_max * 0.7))`` so the engine treats
a capped 35/50 submission as passed (matches the profile mastery
aggregator), while a raw 35/100 submission is still not.
"""

from __future__ import annotations

from app.recommendations.engine import _passed


def test_capped_score_passes_when_meets_effective_max_threshold() -> None:
    # 35 / 50 == 70%; matches the canonical threshold.
    assert _passed(score=35, effective_max=50) is True
    # 34 / 50 == 68%; below threshold.
    assert _passed(score=34, effective_max=50) is False


def test_raw_score_does_not_pass_against_full_denominator() -> None:
    # 35 / 100 == 35%; well below the 70% threshold.
    assert _passed(score=35, effective_max=100) is False
    # 70 / 100 == 70%; at the threshold.
    assert _passed(score=70, effective_max=100) is True


def test_zero_or_negative_effective_max_fails_safe() -> None:
    """A missing / nonsense denominator falls back to "not passed"."""
    assert _passed(score=100, effective_max=0) is False
    assert _passed(score=100, effective_max=-10) is False


def test_non_int_score_fails_safe() -> None:
    """Defensive: a float / None score does not accidentally pass."""
    assert _passed(score=None, effective_max=100) is False  # type: ignore[arg-type]
    assert _passed(score=85.5, effective_max=100) is False  # type: ignore[arg-type]
