"""CLI returns 0 on a valid mission and nonzero on a broken one."""

from __future__ import annotations

from pathlib import Path

from scripts.validate_missions import main as validate_main


def test_cli_succeeds_on_valid(sample_mission_yaml: Path) -> None:
    exit_code = validate_main(["--root", str(sample_mission_yaml.parent)])
    assert exit_code == 0


def test_cli_fails_on_broken(sample_mission_yaml: Path, tmp_path: Path) -> None:
    # Make agent_patch.diff missing → checklist failure.
    (sample_mission_yaml / "agent_patch.diff").unlink()
    exit_code = validate_main(["--root", str(sample_mission_yaml.parent)])
    assert exit_code == 2


def test_cli_fails_on_empty(tmp_path: Path) -> None:
    exit_code = validate_main(["--root", str(tmp_path)])
    assert exit_code == 2
