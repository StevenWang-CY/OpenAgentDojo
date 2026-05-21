"""Grading engine — M5 implementation.

Public surface:

  from app.grading import GradingRunner, GradingResult
  from app.grading.diff import ParsedDiff
  from app.grading.score import ScoreReport, DimensionScore, compute_score
  from app.grading.badges import award
  from app.grading.validators import VALIDATORS, dispatch, ValidatorResult, ...
"""

from __future__ import annotations

from app.grading.runner import GradingResult, GradingRunner

__all__ = ["GradingResult", "GradingRunner"]
