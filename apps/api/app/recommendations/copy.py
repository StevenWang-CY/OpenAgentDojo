"""Deterministic per-dimension diagnosis + per-mission "why" copy (P1-2).

Templated strings only — same inputs produce byte-identical strings on
every replay, which is the load-bearing invariant of the engine's
determinism contract. The LLM polish layer (P1-2 PR2) wraps this with a
read-through cache; on Bedrock 5xx or a cold cache, the engine falls back
to these templates so the recommendation surface never depends on a third-
party 200.

Cold-start vs all-graded vs normal: three template families. Each family
is exhaustive: every dimension key has a string, and the cold-start /
all-graded copy is independent of the dimension so an empty radar (no
graded submissions yet) still renders cleanly.
"""

from __future__ import annotations

from typing import Final, Literal

# Closed set of "modes" the per-mission "why" copy switches on. The
# default ``normal`` path covers cold-start + standard ranking; the
# ``all_graded`` path is taken when the user has graded every shipped
# mission and we're surfacing the largest-gap retry target.
WhyMode = Literal["normal", "all_graded"]

# ---------------------------------------------------------------------------
# Public diagnosis copy.
# ---------------------------------------------------------------------------

COLD_START_DIAGNOSIS: Final[str] = (
    "Start the ladder — these three missions are the orientation path."
)

ALL_GRADED_DIAGNOSIS: Final[str] = (
    "You're solid across all dimensions — try the freshest missions to "
    "keep the edge sharp."
)

# One short, actionable sentence per weakest dimension. Each string names
# the dimension and the underlying habit; the FE renders it verbatim on
# the profile recommendation strip.
_DIAGNOSIS_BY_WEAKEST_DIM: Final[dict[str, str]] = {
    "final_correctness": (
        "Your fixes are landing on the symptom, not the root cause — try "
        "these three missions to practise pinpointing the failing layer."
    ),
    "verification": (
        "You skip the targeted test run more often than not — try these "
        "three missions where verification discipline drives the score."
    ),
    "agent_review": (
        "You glance at the diff instead of reading it — try these three "
        "missions where the agent's narration looks correct but the diff "
        "isn't."
    ),
    "prompt_quality": (
        "Your prompts are too generic to steer the agent — try these "
        "three missions that reward naming files, symbols, and constraints."
    ),
    "context_selection": (
        "You let the agent pick the file set — try these three missions "
        "where opening the wrong files first sends the fix off course."
    ),
    "safety": (
        "You miss the security-relevant edge — try these three missions "
        "where the agent quietly removes a guard."
    ),
    "diff_minimality": (
        "Your diffs are bigger than the fix requires — try these three "
        "missions that reward the smallest possible change."
    ),
}


def diagnosis_for(weakest_dim: str | None) -> str:
    """Return the canonical diagnosis copy for a weakest-dimension key.

    Falls back to a generic "keep practising" sentence when the dimension
    key is unrecognised so a future rubric extension does not crash the
    recommendation surface mid-deploy. The fallback is itself
    deterministic.
    """
    if weakest_dim is None:
        return ALL_GRADED_DIAGNOSIS
    return _DIAGNOSIS_BY_WEAKEST_DIM.get(
        weakest_dim,
        "Keep practising — these three missions are the next step.",
    )


# ---------------------------------------------------------------------------
# Per-mission "why" copy.
# ---------------------------------------------------------------------------

# Dimension labels reused in the "why" templates. Kept inline so a future
# label change in the report copy doesn't silently drift the
# recommendation prose.
_DIM_LABELS: Final[dict[str, str]] = {
    "final_correctness": "final correctness",
    "verification": "verification discipline",
    "agent_review": "agent-output review",
    "prompt_quality": "prompt quality",
    "context_selection": "context selection",
    "safety": "safety awareness",
    "diff_minimality": "diff minimality",
}


def why_for_mission(
    *,
    mission_id: str,
    expected_weak_dim: str | None,
    weakest_dim: str | None,
    alignment: float,
    freshness_fresh: bool = False,
    mode: WhyMode = "normal",
) -> str:
    """Return the deterministic per-mission "why" string.

    The output is keyed off ``(alignment, weakest_dim, expected_weak_dim,
    freshness_fresh, mode)`` so the engine produces stable strings across
    replays. The branches map onto the engine's alignment scores plus the
    two outer modes:

    * ``mode == "all_graded"`` — the user has graded every shipped
      mission; the copy frames it as a retry-the-widest-gap pick.
    * ``mode == "normal"``:
      * ``1.0`` — the mission's expected weak dim matches the user's
        weakest dim. Direct exercise.
      * ``0.5`` — the mission's failure-mode tag maps to the user's
        weakest dim. Indirect but related.
      * ``0.0`` and ``freshness_fresh`` — the mission carries no
        dimension link but IS in the canonical fresh-IDs set. We frame
        the freshness explicitly so the copy reads as "we picked this
        because it's new", not as a vague "keeps the streak going".
      * ``0.0`` otherwise — neutral copy.
    """
    if mode == "all_graded":
        return (
            "your widest gap — give it another pass with what you learned."
        )

    user_label = (
        _DIM_LABELS.get(weakest_dim, weakest_dim)
        if weakest_dim
        else "the freshest dimension on the board"
    )
    if alignment >= 1.0 and expected_weak_dim:
        return (
            f"exercises {user_label} as its primary supervisory axis."
        )
    if alignment >= 0.5 and weakest_dim:
        mission_label = (
            _DIM_LABELS.get(expected_weak_dim, expected_weak_dim)
            if expected_weak_dim
            else "an adjacent skill"
        )
        return (
            f"reinforces {user_label} through a {mission_label} failure mode."
        )
    if freshness_fresh:
        return "fresh on the dojo — try the newest Go pack."
    return (
        f"keeps the streak going at your current difficulty — even when "
        f"the dimension link to {user_label} is indirect."
    )


__all__ = [
    "ALL_GRADED_DIAGNOSIS",
    "COLD_START_DIAGNOSIS",
    "WhyMode",
    "diagnosis_for",
    "why_for_mission",
]
