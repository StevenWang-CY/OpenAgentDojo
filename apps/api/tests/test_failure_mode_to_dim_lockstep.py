"""Lockstep guard between manifest failure-mode vocabulary and engine.

The mission-manifest validator (``app.missions.manifest``) and the
recommendation engine (``app.recommendations.engine``) maintain two
parallel sets:

* ``_FAILURE_MODE_TAGS`` — the closed vocabulary the manifest allows
  on ``mission.tags``. Adding a new failure-mode tag here without
  updating the engine's dim-mapping silently collapses the
  ``dim_alignment`` signal to ``0.0`` for every mission carrying that
  tag — the recommendation ranking degrades silently to "freshness +
  difficulty + novelty" with the dim-aware branch dead.
* ``FAILURE_MODE_TO_DIM`` — the engine's hand-curated tag → rubric-dim
  mapping that drives the 0.5 alignment branch.

This test pins both sets together so a manifest change that adds a new
failure-mode tag MUST also add a row to the engine map (or the test
fails the PR). Removals are caught symmetrically: a tag the engine
maps that the manifest no longer recognises is dead code in the engine
and lights the same failure here.
"""

from __future__ import annotations

from app.missions.manifest import _FAILURE_MODE_TAGS
from app.recommendations.engine import FAILURE_MODE_TO_DIM


def test_failure_mode_to_dim_keys_match_manifest_vocabulary() -> None:
    """Engine's tag→dim map MUST cover exactly the manifest vocabulary.

    Asserts equality (not a subset relation) so the failure surfaces a
    descriptive diff regardless of which side drifted.
    """
    engine_keys = set(FAILURE_MODE_TO_DIM.keys())
    manifest_tags = set(_FAILURE_MODE_TAGS)

    missing_in_engine = manifest_tags - engine_keys
    extra_in_engine = engine_keys - manifest_tags

    assert not missing_in_engine, (
        "Manifest failure-mode tags missing from "
        f"FAILURE_MODE_TO_DIM (engine alignment collapses to 0.0): "
        f"{sorted(missing_in_engine)}"
    )
    assert not extra_in_engine, (
        "FAILURE_MODE_TO_DIM keys not present in manifest vocabulary "
        f"(dead engine code): {sorted(extra_in_engine)}"
    )
