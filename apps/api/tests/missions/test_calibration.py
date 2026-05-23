"""Calibration regression gate (P0-4).

For every mission that has a ``missions/_calibration/<mission_id>.yaml``
file, replay each declared scenario through the grading engine and
assert the actual scores stay within tolerance of the baselines. The
tolerance covers inherent timestamp / rounding jitter; anything larger
is a real scoring change that must be paired with an explicit
calibration update in the same commit.

The first run after `apps/api/scripts/bootstrap_calibration.py` produces
files locks in the *current* grader behaviour. To intentionally change
the baseline:

  1. Make the scoring change.
  2. Run the bootstrap script with --regenerate to refresh the YAMLs.
  3. Inspect the diff and commit both together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.grading.calibration import (
    DIMENSION_TOLERANCE,
    TOTAL_TOLERANCE,
    load_calibration,
)
from app.grading.score import compute_score
from app.missions.loader import MissionLoader
from tests.missions._fixtures import (
    ScoringInputs,
    build_empty_submission,
    build_ideal_submission,
    build_unmodified_submission,
)


def _missions_root() -> Path:
    get_settings.cache_clear()
    return get_settings().missions_root


def _calibration_root() -> Path:
    return _missions_root() / "_calibration"


def _find_mission_folder(mission_id: str) -> Path:
    root = _missions_root()
    loader = MissionLoader(root)
    for m in loader.scan():
        if m.manifest.id == mission_id:
            return m.folder
    raise FileNotFoundError(
        f"mission {mission_id!r} has a calibration file but no on-disk folder"
    )


def _build_inputs(scenario: str, manifest, folder: Path) -> ScoringInputs:
    if scenario == "unmodified":
        return build_unmodified_submission(manifest, folder)
    if scenario == "ideal":
        return build_ideal_submission(manifest, folder)
    if scenario == "empty":
        return build_empty_submission(manifest, folder)
    raise ValueError(f"unknown scenario {scenario!r}")


def _collect_calibration_pairs() -> list[tuple[str, str, Path, Path]]:
    """Return ``(mission_id, scenario_name, mission_folder, calibration_path)``
    for every (mission, scenario) tuple declared in the calibration set."""
    calib_dir = _calibration_root()
    if not calib_dir.exists():
        return []
    pairs: list[tuple[str, str, Path, Path]] = []
    for yaml_path in sorted(calib_dir.glob("*.yaml")):
        cal = load_calibration(yaml_path)
        try:
            folder = _find_mission_folder(cal.mission_id)
        except FileNotFoundError:
            continue
        for sc in cal.scenarios:
            pairs.append((cal.mission_id, sc.name, folder, yaml_path))
    return pairs


_PAIRS = _collect_calibration_pairs()


@pytest.mark.skipif(not _PAIRS, reason="no calibration files present")
@pytest.mark.parametrize(
    ("mission_id", "scenario", "folder", "calib_path"),
    _PAIRS,
    ids=[f"{m}::{s}" for m, s, _, _ in _PAIRS],
)
def test_calibration_scenario_within_tolerance(
    mission_id: str, scenario: str, folder: Path, calib_path: Path
) -> None:
    cal = load_calibration(calib_path)
    target = next(s for s in cal.scenarios if s.name == scenario)
    loader = MissionLoader(folder.parent)
    manifest = loader._load_one(folder / "mission.yaml").manifest

    inputs = _build_inputs(scenario, manifest, folder)
    report = compute_score(
        diff=inputs.diff,
        events=inputs.events,
        validator_results=inputs.validator_results,
        test_results=inputs.test_results,
        manifest=manifest,
        agent_turns=inputs.agent_turns,
    )

    # Total within tolerance.
    diff_total = abs(report.total - target.expected_total)
    assert diff_total <= TOTAL_TOLERANCE, (
        f"[{mission_id}::{scenario}] total {report.total} drifted by "
        f"{diff_total} from baseline {target.expected_total} "
        f"(tolerance {TOTAL_TOLERANCE}); "
        f"regenerate calibration if the change is intentional"
    )

    # Each dimension within tolerance.
    expected_dims = target.dimensions.as_dict()
    drift_lines: list[str] = []
    for dim_name, expected in expected_dims.items():
        actual_ds = report.dimensions.get(dim_name)
        actual = (
            actual_ds.score
            if actual_ds is not None and not actual_ds.pending
            else 0
        )
        if abs(actual - expected) > DIMENSION_TOLERANCE:
            drift_lines.append(
                f"  {dim_name}: expected {expected}, got {actual}"
            )
    assert not drift_lines, (
        f"[{mission_id}::{scenario}] per-dimension drift exceeds "
        f"{DIMENSION_TOLERANCE} point(s):\n" + "\n".join(drift_lines)
    )
