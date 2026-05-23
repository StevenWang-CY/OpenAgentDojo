#!/usr/bin/env python3
"""Bootstrap (or regenerate) the calibration baseline files (P0-4).

Runs the actual grading engine against each of the 10 missions in each
of the three standard scenarios (unmodified / ideal / empty), captures
the per-dimension scores, and writes one
``missions/_calibration/<mission_id>.yaml`` per mission.

Usage:
  uv run python scripts/bootstrap_calibration.py            # fill in missing files only
  uv run python scripts/bootstrap_calibration.py --regenerate   # overwrite existing files
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_API_DIR = _SCRIPT_DIR.parent
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.config import get_settings
from app.grading.calibration import (
    SCENARIO_NAMES,
    CalibrationDimensions,
    CalibrationFile,
    CalibrationScenario,
    dump_calibration_yaml,
)
from app.grading.score import compute_score
from app.missions.loader import MissionLoader
from tests.missions._fixtures import (
    build_empty_submission,
    build_ideal_submission,
    build_unmodified_submission,
)


def _build_inputs(scenario: str, manifest, folder: Path):
    if scenario == "unmodified":
        return build_unmodified_submission(manifest, folder)
    if scenario == "ideal":
        return build_ideal_submission(manifest, folder)
    if scenario == "empty":
        return build_empty_submission(manifest, folder)
    raise ValueError(f"unknown scenario {scenario!r}")


def _build_baseline(mission_id: str, mission_version: int, folder: Path, manifest) -> CalibrationFile:
    scenarios: list[CalibrationScenario] = []
    for scenario in SCENARIO_NAMES:
        inputs = _build_inputs(scenario, manifest, folder)
        report = compute_score(
            diff=inputs.diff,
            events=inputs.events,
            validator_results=inputs.validator_results,
            test_results=inputs.test_results,
            manifest=manifest,
            agent_turns=inputs.agent_turns,
        )
        dim_scores = {
            name: max(0, ds.score) if not ds.pending else 0
            for name, ds in report.dimensions.items()
        }
        scenarios.append(
            CalibrationScenario(
                name=scenario,
                expected_total=int(report.total),
                source="bootstrap",
                dimensions=CalibrationDimensions.from_dict(dim_scores),
                graders=[],
            )
        )
    return CalibrationFile(
        mission_id=mission_id,
        mission_version=mission_version,
        scenarios=scenarios,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="overwrite existing calibration files (default: skip them)",
    )
    args = parser.parse_args(argv)

    get_settings.cache_clear()
    root: Path = get_settings().missions_root
    calib_dir = root / "_calibration"
    calib_dir.mkdir(parents=True, exist_ok=True)

    loader = MissionLoader(root)
    missions = loader.scan()
    wrote = skipped = 0
    for m in missions:
        out_path = calib_dir / f"{m.manifest.id}.yaml"
        if out_path.exists() and not args.regenerate:
            print(f"SKIP {m.manifest.id} (file exists; --regenerate to overwrite)")
            skipped += 1
            continue
        calib = _build_baseline(
            m.manifest.id, int(m.manifest.version), m.folder, m.manifest
        )
        out_path.write_text(dump_calibration_yaml(calib), encoding="utf-8")
        print(
            f"WROTE {m.manifest.id} → {out_path.relative_to(root.parent)}"
        )
        wrote += 1
    print(f"\n{wrote} written, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
