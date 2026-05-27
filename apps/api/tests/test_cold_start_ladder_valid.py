"""Cold-start ladder mission ids must resolve to real published missions.

The engine pins :data:`app.recommendations.engine.INTRODUCTORY_LADDER`
as a hardcoded tuple of three mission ids (the cold-start path returns
exactly these in order). The pin exists so cold-start is deterministic
regardless of row ordering in ``missions``, but it has a sharp edge:
if a manifest rename / unpublish lands without updating the ladder,
the cold-start path silently returns ZERO recommendations (the engine
skips ids not present in the catalogue, see ``_cold_start``).

This test reads the mission YAML tree on disk (the source of truth
for publication state) and asserts every ladder id exists and is
published. Catching the drift at the test boundary means a rename
that misses the engine constant fails the PR rather than degrading
silently in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.recommendations.engine import INTRODUCTORY_LADDER

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MISSIONS_ROOT = _REPO_ROOT / "missions"


def _collect_published_mission_ids() -> dict[str, dict]:
    """Return ``{mission_id: manifest_dict}`` for every published mission.

    Walks the on-disk ``missions/*/mission.yaml`` tree. A mission with
    ``published: false`` (or the field missing — published defaults to
    true in the manifest loader, but we mirror the loader semantics
    here) is included; the engine reads the DB which mirrors the YAML,
    so the YAML is the canonical source of truth at test time.
    """
    out: dict[str, dict] = {}
    if not _MISSIONS_ROOT.exists():
        pytest.skip(f"missions tree not present at {_MISSIONS_ROOT}")
    for manifest_path in sorted(_MISSIONS_ROOT.glob("*/mission.yaml")):
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        mid = data.get("id")
        if not isinstance(mid, str):
            continue
        out[mid] = data
    return out


def test_introductory_ladder_ids_exist_in_catalogue() -> None:
    """Every id in INTRODUCTORY_LADDER must resolve to a real mission.

    Without this guard, a manifest rename silently breaks cold-start:
    the engine skips unknown ids and returns an empty
    ``recommendations`` list, which the FE renders as "no cards"
    instead of the introductory ladder.
    """
    catalogue = _collect_published_mission_ids()
    missing = [mid for mid in INTRODUCTORY_LADDER if mid not in catalogue]
    assert not missing, (
        "INTRODUCTORY_LADDER references missions that do not exist on "
        f"disk: {missing}. Update the constant in "
        "app/recommendations/engine.py or restore the manifest."
    )


def test_introductory_ladder_ids_are_published_standard_missions() -> None:
    """Cold-start ladder entries must be published, standard missions.

    A tutorial (``kind: tutorial``) or unpublished mission silently
    falls out of the engine's published-mission filter — the
    cold-start path then renders fewer than three cards. This guard
    pins the publication-state invariant alongside the id-exists
    check.
    """
    catalogue = _collect_published_mission_ids()
    issues: list[str] = []
    for mid in INTRODUCTORY_LADDER:
        manifest = catalogue.get(mid)
        if manifest is None:
            continue  # covered by the previous test
        if manifest.get("published") is False:
            issues.append(f"{mid}: published=false")
        if manifest.get("kind") == "tutorial":
            issues.append(f"{mid}: kind=tutorial (must be standard)")
    assert not issues, (
        "INTRODUCTORY_LADDER entries fail the published-standard "
        f"invariant: {issues}"
    )
