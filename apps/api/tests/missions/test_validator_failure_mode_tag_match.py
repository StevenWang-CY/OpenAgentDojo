"""P1-1 — the failure-mode tag MUST equal ``failure_mode.id``.

A standard mission carries exactly one failure-mode tag, and that token
must equal the manifest's ``failure_mode.id`` so the catalog filter never
drifts from the report copy. The manifest's
``_failure_mode_tag_matches_manifest`` model validator enforces this; we
exercise both surfaces (loader + raw manifest) so a future schema
refactor that loses one of them is caught.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.missions.loader import MissionLoader
from app.missions.manifest import MissionConfigError, MissionManifest

# Pydantic wraps any exception raised from a ``@model_validator(mode="after")``
# in a ``ValidationError``; ``MissionConfigError`` survives intact when the
# loader catches the wrapped form and re-raises the inner error. We accept
# either type so the contract test stays load-bearing on the message content
# rather than on which layer surfaced it.
_MissionFailure = (MissionConfigError, ValidationError)

_MISSIONS_ROOT = Path(__file__).resolve().parents[3].parent / "missions"
_EXEMPLAR = "01-auth-cookie-expiration"


def _stage_mission(tmp_path: Path) -> Path:
    src = _MISSIONS_ROOT / _EXEMPLAR
    if not src.is_dir():
        pytest.skip(f"exemplar mission folder missing: {src}")
    dst = tmp_path / _EXEMPLAR
    shutil.copytree(src, dst)
    return dst


def test_failure_mode_id_must_match_tag_via_loader(tmp_path: Path) -> None:
    """Manifest with mismatched failure_mode.id vs. tag must trip loader."""
    staged = _stage_mission(tmp_path)
    mission_yaml = staged / "mission.yaml"
    body = mission_yaml.read_text(encoding="utf-8")
    # Flip the failure_mode.id to a DIFFERENT valid failure-mode token; the
    # tag stays at ``checks_presence_not_expiration`` so the two no longer
    # agree. We pick ``overfitted_visible_test`` because it is in the
    # closed vocabulary but is not also present as a tag here.
    mutated = body.replace(
        "  id: checks_presence_not_expiration\n",
        "  id: overfitted_visible_test\n",
    )
    assert mutated != body, "expected to mutate the failure_mode.id line"
    mission_yaml.write_text(mutated, encoding="utf-8")

    loader = MissionLoader(tmp_path)
    with pytest.raises(MissionConfigError) as excinfo:
        loader.validate_all()
    msg = str(excinfo.value)
    assert "checks_presence_not_expiration" in msg
    assert "overfitted_visible_test" in msg


def test_missing_failure_mode_tag_rejected(tmp_path: Path) -> None:
    """A standard mission with NO failure-mode tag must raise."""
    base = (
        _MISSIONS_ROOT / _EXEMPLAR / "mission.yaml"
    ).read_text(encoding="utf-8")
    # Strip out the failure-mode tag, keeping the skill + lang tags.
    body = base.replace(
        "  - checks_presence_not_expiration\n  - skill:auth\n",
        "  - skill:auth\n",
    )
    import yaml

    data = yaml.safe_load(body)
    with pytest.raises(_MissionFailure) as excinfo:
        MissionManifest.model_validate(data)
    assert "failure-mode tag" in str(excinfo.value)


def test_multiple_failure_mode_tags_rejected(tmp_path: Path) -> None:
    """A standard mission with two failure-mode tags is ambiguous."""
    base = (
        _MISSIONS_ROOT / _EXEMPLAR / "mission.yaml"
    ).read_text(encoding="utf-8")
    body = base.replace(
        "  - checks_presence_not_expiration\n  - skill:auth\n",
        "  - checks_presence_not_expiration\n  - race_condition\n  - skill:auth\n",
    )
    import yaml

    data = yaml.safe_load(body)
    with pytest.raises(_MissionFailure) as excinfo:
        MissionManifest.model_validate(data)
    msg = str(excinfo.value)
    assert "multiple failure-mode tags" in msg
