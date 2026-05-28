"""P1-1 — every mission tag must come from the closed vocabulary.

The manifest's ``_tags_in_closed_vocabulary`` field validator (see
``apps/api/app/missions/manifest.py``) rejects any token outside the
failure-mode / ``skill:*`` / ``lang:*`` families. The mission loader
surfaces the same error through ``validate_all``; this test pins both
surfaces so a future schema refactor that loses the gate is caught.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.missions.loader import MissionLoader
from app.missions.manifest import MissionConfigError, MissionManifest

_MISSIONS_ROOT = Path(__file__).resolve().parents[3].parent / "missions"
_EXEMPLAR = "01-auth-cookie-expiration"


def _stage_mission(tmp_path: Path) -> Path:
    src = _MISSIONS_ROOT / _EXEMPLAR
    if not src.is_dir():
        pytest.skip(f"exemplar mission folder missing: {src}")
    dst = tmp_path / _EXEMPLAR
    shutil.copytree(src, dst)
    return dst


def _inject_unknown_tag(mission_yaml: Path, bogus: str) -> None:
    """Replace the `tags:` block in mission.yaml with an unknown-token entry."""
    body = mission_yaml.read_text(encoding="utf-8")
    # Keep the failure-mode tag (so the failure-mode-match validator
    # does not also fire) but slip the unknown token in alongside.
    new_tags_block = (
        "tags:\n"
        "  - checks_presence_not_expiration\n"
        f"  - {bogus}\n"
    )
    # The exemplar carries a contiguous ``tags:`` block; replace it in
    # full. We anchor on the trailing ``- lang:typescript`` line which is
    # the last token of the block in the exemplar manifest.
    target = (
        "tags:\n"
        "  - checks_presence_not_expiration\n"
        "  - skill:auth\n"
        "  - lang:typescript\n"
    )
    if target not in body:
        raise AssertionError(
            "exemplar mission.yaml no longer carries the expected tags "
            "block; update test_validator_closed_vocab_tags.py"
        )
    mission_yaml.write_text(body.replace(target, new_tags_block), encoding="utf-8")


def test_unknown_tag_rejected_by_validator(tmp_path: Path) -> None:
    """Loading a mission with an unknown tag must raise."""
    staged = _stage_mission(tmp_path)
    _inject_unknown_tag(staged / "mission.yaml", "skill:typeshare")

    loader = MissionLoader(tmp_path)
    with pytest.raises(MissionConfigError) as excinfo:
        loader.validate_all()
    msg = str(excinfo.value)
    # Surface the offending token directly so the author sees what to fix.
    assert "skill:typeshare" in msg


def test_unknown_tag_rejected_at_manifest_layer(tmp_path: Path) -> None:
    """The Pydantic field validator itself rejects the unknown token.

    Caught one layer below the loader — guards against a future refactor
    that splits manifest parsing from on-disk discovery.
    """
    base = (
        _MISSIONS_ROOT / _EXEMPLAR / "mission.yaml"
    ).read_text(encoding="utf-8")
    bad = base.replace(
        "  - lang:typescript\n",
        "  - lang:typescript\n  - made_up_failure_mode\n",
    )
    import yaml

    data = yaml.safe_load(bad)
    with pytest.raises(Exception) as excinfo:
        MissionManifest.model_validate(data)
    assert "made_up_failure_mode" in str(excinfo.value)
