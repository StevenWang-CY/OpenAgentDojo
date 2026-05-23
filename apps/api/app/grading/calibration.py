"""Calibration set loader (P0-4).

The calibration set lives under ``missions/_calibration/`` (see the
README there). This module loads each ``<mission_id>.yaml``, computes
the expected score for each declared scenario by replaying it through
the actual grading engine, and exposes the per-dimension tolerances the
calibration test enforces.

Why: replays of the same scenario must produce the same score, but
manifest bumps + scoring-rule changes need to roll through the
calibration baselines explicitly. The CI test gate refuses to merge a
change that drifts a calibration scenario by more than the tolerance
unless the calibration file is also updated in the same commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# How much the grader's actual total may diverge from a calibration
# baseline before the test gate fails. Five points covers the inherent
# rounding noise across the seven dimensions; anything larger is a
# substantive scoring change that should be explicitly recorded.
TOTAL_TOLERANCE: int = 5

# Per-dimension tolerance. Two points covers signal jitter (e.g. a
# one-second timestamp drift bumping the dwell-time signal in or out of
# the +6 band) without masking real regressions.
DIMENSION_TOLERANCE: int = 2

SCENARIO_NAMES = ("unmodified", "ideal", "empty")


@dataclass
class CalibrationDimensions:
    """Per-dimension expected scores for one scenario."""

    final_correctness: int
    verification: int
    agent_review: int
    prompt_quality: int
    context_selection: int
    safety: int
    diff_minimality: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationDimensions:
        return cls(
            final_correctness=int(data["final_correctness"]),
            verification=int(data["verification"]),
            agent_review=int(data["agent_review"]),
            prompt_quality=int(data["prompt_quality"]),
            context_selection=int(data["context_selection"]),
            safety=int(data["safety"]),
            diff_minimality=int(data["diff_minimality"]),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "final_correctness": self.final_correctness,
            "verification": self.verification,
            "agent_review": self.agent_review,
            "prompt_quality": self.prompt_quality,
            "context_selection": self.context_selection,
            "safety": self.safety,
            "diff_minimality": self.diff_minimality,
        }


@dataclass
class CalibrationScenario:
    name: str
    expected_total: int
    source: str  # "bootstrap" | "human_median"
    dimensions: CalibrationDimensions
    graders: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CalibrationFile:
    mission_id: str
    mission_version: int
    scenarios: list[CalibrationScenario]


def load_calibration(path: Path) -> CalibrationFile:
    """Parse one ``missions/_calibration/<mission_id>.yaml`` file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"calibration file {path} is not a mapping")
    scenarios_raw = raw.get("scenarios") or []
    scenarios: list[CalibrationScenario] = []
    for entry in scenarios_raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name not in SCENARIO_NAMES:
            raise ValueError(
                f"calibration scenario name {name!r} in {path} not in {SCENARIO_NAMES}"
            )
        dims = CalibrationDimensions.from_dict(entry.get("dimensions") or {})
        scenarios.append(
            CalibrationScenario(
                name=str(name),
                expected_total=int(entry.get("expected_total", sum(dims.as_dict().values()))),
                source=str(entry.get("source", "bootstrap")),
                dimensions=dims,
                graders=list(entry.get("graders") or []),
            )
        )
    return CalibrationFile(
        mission_id=str(raw.get("mission_id", path.stem)),
        mission_version=int(raw.get("mission_version", 1)),
        scenarios=scenarios,
    )


def dump_calibration_yaml(c: CalibrationFile) -> str:
    """Render a CalibrationFile back to canonical YAML (used by the
    bootstrap script when regenerating baselines)."""
    payload: dict[str, Any] = {
        "mission_id": c.mission_id,
        "mission_version": c.mission_version,
        "scenarios": [
            {
                "name": s.name,
                "expected_total": s.expected_total,
                "source": s.source,
                "dimensions": s.dimensions.as_dict(),
                "graders": s.graders,
            }
            for s in c.scenarios
        ],
    }
    return str(yaml.safe_dump(payload, sort_keys=False))
