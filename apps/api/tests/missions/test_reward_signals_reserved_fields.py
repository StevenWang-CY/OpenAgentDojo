"""P1/P2 — the reserved ``reward_signals`` fields are genuinely inert.

Several ``reward_signals`` sub-fields (and ``ValidatorNoNewDependencies.allowed``)
are parsed + schema-validated but NOT consumed by ``app.grading.score``. The
manifest module documents this explicitly (the RESERVED-FIELD POLICY note +
inline ``RESERVED`` tags) so the drift is no longer silent. This test pins the
honest contract: mutating any reserved field on a real manifest must NOT change
the computed total. If a future change wires one of these fields into the
grader, this test will fail — at which point the RESERVED annotation must be
removed (and the calibration envelopes re-reviewed) in the same change.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.config import get_settings
from app.grading.score import compute_score
from app.missions.loader import MissionLoader
from tests.missions._fixtures import build_ideal_submission

# A real curated mission that declares every reserved field in its
# ``reward_signals`` block (validators + all four reward sections).
_EXEMPLAR_ID = "05-security-validation-removed"


def _missions_root() -> Path:
    get_settings.cache_clear()
    return get_settings().missions_root


def _load_exemplar():
    root = _missions_root()
    folder = root / _EXEMPLAR_ID
    if not folder.is_dir():
        pytest.skip(f"exemplar mission folder missing: {folder}")
    loader = MissionLoader(root)
    loaded = loader._load_one(folder / "mission.yaml")
    return loaded.manifest, folder


def _total_for(manifest, folder: Path) -> int:
    inputs = build_ideal_submission(manifest, folder)
    report = compute_score(
        diff=inputs.diff,
        events=inputs.events,
        validator_results=inputs.validator_results,
        test_results=inputs.test_results,
        manifest=manifest,
        agent_turns=inputs.agent_turns,
    )
    return report.total


def test_reserved_reward_signal_mutations_do_not_move_the_score() -> None:
    """Flipping every RESERVED field leaves the computed total unchanged."""
    manifest, folder = _load_exemplar()
    baseline = _total_for(manifest, folder)

    mutated = copy.deepcopy(manifest)
    rs = mutated.reward_signals

    # PromptQualitySignals.penalty_if_under_chars — RESERVED.
    rs.prompt_quality.penalty_if_under_chars = 999
    # VerificationSignals.required_categories / bonus_if_run_before_patch.
    rs.verification.required_categories = ["lint", "typecheck", "test", "e2e"]
    rs.verification.bonus_if_run_before_patch = not rs.verification.bonus_if_run_before_patch
    # AgentReviewSignals.require_diff_open / bonus_if_revert_or_edit_after_diff.
    rs.agent_review.require_diff_open = not rs.agent_review.require_diff_open
    rs.agent_review.bonus_if_revert_or_edit_after_diff = (
        not rs.agent_review.bonus_if_revert_or_edit_after_diff
    )
    # SafetySignals.must_not_introduce_deps — RESERVED.
    rs.safety.must_not_introduce_deps = not rs.safety.must_not_introduce_deps

    mutated_total = _total_for(mutated, folder)
    assert mutated_total == baseline, (
        "a RESERVED reward_signals field moved the score — it is no longer "
        "inert; update the RESERVED annotation in manifest.py and re-review "
        f"the calibration envelopes (baseline={baseline}, mutated={mutated_total})"
    )


def test_consumed_prompt_keywords_still_move_the_score() -> None:
    """Sanity guard: a CONSUMED field still affects the score.

    Without this, ``test_reserved_...`` could pass trivially if the scorer
    ignored the entire ``reward_signals`` block. Clearing the prompt-quality
    keyword sets is a CONSUMED change, so the ideal-submission total must
    differ from a run that keeps them.
    """
    manifest, folder = _load_exemplar()
    baseline = _total_for(manifest, folder)

    mutated = copy.deepcopy(manifest)
    # ``bonus_keywords`` is CONSUMED (score.py reads it for prompt quality).
    # The ideal prompt embeds these keywords, so removing them must lower the
    # prompt-quality contribution and therefore the total.
    mutated.reward_signals.prompt_quality.bonus_keywords = []
    mutated.reward_signals.prompt_quality.must_include_any = []

    mutated_total = _total_for(mutated, folder)
    assert mutated_total != baseline, (
        "clearing CONSUMED prompt keyword sets did not change the score — "
        "the reserved-field inertness test would be vacuous"
    )
