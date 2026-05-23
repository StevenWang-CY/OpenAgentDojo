"""Mission self-tests — score envelopes (IMPLEMENTATION_PLAN.md §19.1).

For every mission under ``/missions``, replay three synthetic supervisor
event streams against the scoring engine and assert the resulting total
falls in the band declared by that mission's ``acceptance.yaml``:

* ``unmodified``  — agent patch submitted verbatim with only a perfunctory
  test run. Total must land in ``[min_unmodified, max_unmodified]``.
* ``ideal``       — minimal fix + regression test + full review loop.
  Total must be ``>= min_ideal``.
* ``empty``       — empty diff and no review activity. Total must be
  ``<= max_empty`` (default 15).

Scope and limits
----------------
This harness is a **deterministic replay** of canned signals against
:func:`app.grading.score.compute_score`. It does NOT exercise the Docker
sandbox, the local sandbox driver, the real test runners, the agent, or
the validator dispatch pipeline. Those layers are covered by their own
test files (``test_grading_runner.py``, ``test_freeze_and_grade_local.py``,
``tests/grading/``).

What this harness *does* guarantee:

* every mission's ``acceptance.yaml`` parses with the current Pydantic
  model (including the optional ``max_empty`` field);
* every mission's manifest survives :class:`MissionLoader.scan()`;
* the scoring engine, given representative inputs, produces totals
  consistent with the documented envelopes — so a future edit to
  ``score.py`` that drifts the totals for any mission will be flagged
  here instead of in production.

If you tune the scoring engine and a mission moves out of band, you
should either (a) explain the drift and update the envelope in that
mission's ``acceptance.yaml``, or (b) revert the engine change. Do not
silently widen the bands — the embarrassing-middle floor is load-bearing
for the supervision narrative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.grading.score import compute_score
from app.missions.acceptance import load_acceptance
from app.missions.loader import MissionLoader
from tests.missions._fixtures import (
    ScoringInputs,
    build_empty_submission,
    build_ideal_submission,
    build_unmodified_submission,
)

# ---------------------------------------------------------------------------
# Collection: discover all 10 (non-draft) missions on disk.
# ---------------------------------------------------------------------------


def _missions_root() -> Path:
    get_settings.cache_clear()
    return get_settings().missions_root


def _collect_missions() -> list[tuple[str, Path]]:
    """Return ``(mission_id, folder)`` for every score-graded mission on disk.

    Tutorial missions (P0-1) are graded by completion, not score, so they
    ship no ``acceptance.yaml`` and are intentionally excluded here.
    """
    root = _missions_root()
    loader = MissionLoader(root)
    return [
        (m.manifest.id, m.folder)
        for m in loader.scan()
        if getattr(m.manifest, "kind", "standard") != "tutorial"
    ]


_MISSIONS = _collect_missions()
_MISSION_IDS = [mid for mid, _ in _MISSIONS]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _load_manifest_and_envelope(folder: Path):
    loader = MissionLoader(folder.parent)
    loaded = loader._load_one(folder / "mission.yaml")
    envelope = load_acceptance(folder / "acceptance.yaml").acceptance
    return loaded.manifest, envelope


def _score(inputs: ScoringInputs, manifest) -> int:
    report = compute_score(
        diff=inputs.diff,
        events=inputs.events,
        validator_results=inputs.validator_results,
        test_results=inputs.test_results,
        manifest=manifest,
        agent_turns=inputs.agent_turns,
    )
    return report.total


def _format_breakdown(inputs: ScoringInputs, manifest) -> str:
    """Render a per-dimension breakdown to surface in assertion messages."""
    report = compute_score(
        diff=inputs.diff,
        events=inputs.events,
        validator_results=inputs.validator_results,
        test_results=inputs.test_results,
        manifest=manifest,
        agent_turns=inputs.agent_turns,
    )
    rows = [f"  total={report.total}"]
    for name, dim in report.dimensions.items():
        rows.append(f"  {name:20s} {dim.score:>3d}/{dim.max_score:<3d}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Sanity: at least one mission was discovered.
# ---------------------------------------------------------------------------


def test_missions_were_discovered() -> None:
    assert _MISSION_IDS, "no missions discovered under MISSIONS_ROOT"
    # M6 ships 10 missions; if this trips, content was removed or the
    # discovery glob broke.
    assert len(_MISSION_IDS) >= 10, (
        f"expected >= 10 missions, found {len(_MISSION_IDS)}: {_MISSION_IDS}"
    )


# ---------------------------------------------------------------------------
# Per-mission envelope tests.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mission_id,folder", _MISSIONS, ids=_MISSION_IDS)
def test_unmodified_band(mission_id: str, folder: Path) -> None:
    manifest, env = _load_manifest_and_envelope(folder)
    inputs = build_unmodified_submission(manifest, folder)
    total = _score(inputs, manifest)
    assert env.min_unmodified <= total <= env.max_unmodified, (
        f"unmodified replay for {mission_id} scored {total}, "
        f"expected {env.min_unmodified}..{env.max_unmodified}\n"
        f"{_format_breakdown(inputs, manifest)}"
    )


@pytest.mark.parametrize("mission_id,folder", _MISSIONS, ids=_MISSION_IDS)
def test_ideal_floor(mission_id: str, folder: Path) -> None:
    manifest, env = _load_manifest_and_envelope(folder)
    inputs = build_ideal_submission(manifest, folder)
    total = _score(inputs, manifest)
    assert total >= env.min_ideal, (
        f"ideal replay for {mission_id} scored {total}, "
        f"expected >= {env.min_ideal}\n"
        f"{_format_breakdown(inputs, manifest)}"
    )


@pytest.mark.parametrize("mission_id,folder", _MISSIONS, ids=_MISSION_IDS)
def test_empty_ceiling(mission_id: str, folder: Path) -> None:
    manifest, env = _load_manifest_and_envelope(folder)
    inputs = build_empty_submission(manifest, folder)
    total = _score(inputs, manifest)
    assert total <= env.max_empty, (
        f"empty replay for {mission_id} scored {total}, "
        f"expected <= {env.max_empty}\n"
        f"{_format_breakdown(inputs, manifest)}"
    )
