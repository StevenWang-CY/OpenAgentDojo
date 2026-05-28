"""P1-1 — the mission loader MUST refuse to load a mission whose folder
still carries an un-promoted ``_draft/`` subdirectory.

The LLM-assisted authoring scaffold (``app.llm.cli``) writes candidate
artefacts there for hand-review; if the loader silently picked up the
mission anyway, generator output would ship unreviewed. This test
captures the contract:

* :class:`MissionLoader.validate_all` raises :class:`MissionConfigError`
  with the offending mission id surfaced in the message.

* :class:`MissionLoader.scan` (the non-strict variant used by the runtime
  upsert) logs and skips the same folder so the catalog never tries to
  upsert a draft mission.

* The rejection is keyed on ``_draft/`` being a directory; a regular file
  named ``_draft`` is benign (and historically used as a soft marker by
  the older ``.draft`` flag the loader has always honoured).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.missions.loader import MissionLoader
from app.missions.manifest import MissionConfigError

_MISSIONS_ROOT = Path(__file__).resolve().parents[3].parent / "missions"
_EXEMPLAR = "01-auth-cookie-expiration"


def _stage_mission(tmp_path: Path) -> Path:
    """Copy the exemplar mission folder into ``tmp_path`` so we can mutate it.

    Returns the path of the staged mission folder (the parent ``tmp_path``
    is the "missions root" the loader will scan).
    """
    src = _MISSIONS_ROOT / _EXEMPLAR
    if not src.is_dir():
        pytest.skip(f"exemplar mission folder missing: {src}")
    dst = tmp_path / _EXEMPLAR
    shutil.copytree(src, dst)
    return dst


def test_validate_all_rejects_draft_subdirectory(tmp_path: Path) -> None:
    """A mission folder with ``_draft/`` must trip ``validate_all``."""
    staged = _stage_mission(tmp_path)
    (staged / "_draft").mkdir()
    # Leave a sentinel file inside so the directory is non-empty (matches
    # the real-world shape the LLM CLI emits).
    (staged / "_draft" / "ideal_solution.draft.md").write_text(
        "DRAFT", encoding="utf-8"
    )

    loader = MissionLoader(tmp_path)
    with pytest.raises(MissionConfigError) as excinfo:
        loader.validate_all()
    msg = str(excinfo.value)
    assert "_draft" in msg
    # The mission id surfaces in the message so a CI log line is
    # actionable without a stack-trace dive.
    assert "auth-cookie-expiration" in msg


def test_scan_skips_draft_mission_silently(tmp_path: Path, caplog) -> None:  # type: ignore[no-untyped-def]
    """Non-strict ``scan`` logs and drops the mission rather than raising.

    The runtime upsert path uses ``scan``; a single broken/drafted mission
    must NOT zero the catalog, so we expect the loader to drop the entry
    and return an empty list when only the drafted mission is present.
    """
    staged = _stage_mission(tmp_path)
    (staged / "_draft").mkdir()

    loader = MissionLoader(tmp_path)
    loaded = loader.scan()
    assert loaded == [], (
        "scan() must drop the drafted mission, not surface it as a loaded "
        "catalog row; got: " + repr([m.manifest.id for m in loaded])
    )


def test_regular_file_named_underscore_draft_does_not_trip(
    tmp_path: Path,
) -> None:
    """A *file* (not directory) named ``_draft`` is benign.

    The contract intentionally keys on ``is_dir()`` because the scaffold
    only ever writes a directory; a stray ``_draft`` text file in a
    mission folder should not block loading.
    """
    staged = _stage_mission(tmp_path)
    (staged / "_draft").write_text("not a directory", encoding="utf-8")

    loader = MissionLoader(tmp_path)
    loaded = loader.validate_all()
    assert any(m.manifest.id == "auth-cookie-expiration" for m in loaded)
