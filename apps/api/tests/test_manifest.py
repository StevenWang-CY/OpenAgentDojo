"""MissionManifest parsing + scoring-weight enforcement."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from app.missions.manifest import MissionManifest


def test_valid_manifest_loads(sample_mission_yaml) -> None:
    raw = (sample_mission_yaml / "mission.yaml").read_text()
    data = yaml.safe_load(raw)
    manifest = MissionManifest.model_validate(data)
    assert manifest.id == "sample-mission"
    assert manifest.scoring_weights.final_correctness == 30


def test_invalid_id_rejected(sample_mission_yaml) -> None:
    raw = (sample_mission_yaml / "mission.yaml").read_text()
    data = yaml.safe_load(raw)
    data["id"] = "Invalid_ID"  # uppercase + underscore — rejected by pattern
    with pytest.raises(ValidationError):
        MissionManifest.model_validate(data)


def test_scoring_weights_enforced(sample_mission_yaml) -> None:
    raw = (sample_mission_yaml / "mission.yaml").read_text()
    data = yaml.safe_load(raw)
    data["scoring_weights"]["final_correctness"] = 25  # wrong
    with pytest.raises(ValidationError) as excinfo:
        MissionManifest.model_validate(data)
    assert "scoring_weights.final_correctness" in str(excinfo.value)


def test_expected_context_required_min(sample_mission_yaml) -> None:
    raw = (sample_mission_yaml / "mission.yaml").read_text()
    data = yaml.safe_load(raw)
    data["expected_context"]["required"] = ["only-one.ts"]
    with pytest.raises(ValidationError):
        MissionManifest.model_validate(data)
