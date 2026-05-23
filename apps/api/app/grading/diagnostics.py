"""Per-dimension diagnostic + next-mission recommendation generator (P2-1).

The pre-P2-1 ``ScoreReport`` exposed only ``strengths`` / ``weaknesses``
as ``"{label}: {score}/{max}"`` strings — a number with no diagnosis and
no next step. This module reads the raw signals each dimension already
emits and synthesises a structured :class:`Diagnostic` per weakness:

  * the likely *cause* of the low score (derived from signal patterns)
  * a concrete *recommendation*, including which other missions are
    designed to exercise the weak dimension.

Inputs:
  * the dimension's ``DimensionScore`` (score, max, signals)
  * the mission catalog (to pick the recommendation set)
  * the user's already-completed mission ids (so we don't recommend
    missions they have already attempted)

The map of dimension → mission ids is curated by hand: each mission is
designed to exercise specific supervision skills, so the recommendation
table is part of the rubric, not derived from data. Bumping
:data:`RECOMMENDATION_VERSION` flags downstream consumers that the
recommendation logic has changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Bump when the recommendation table changes — consumers can use this to
# invalidate UI badges like "you've been recommended Mission 05 5 times".
RECOMMENDATION_VERSION: int = 1

# Mission ids that exercise each supervision dimension. Hand-curated from
# the mission catalog; see missions/<id>/README.md for the rationale.
#
# The order within each list is the recommendation priority — first entry
# is the most direct exercise of that dimension, last entry is a
# supporting drill. We surface up to 2 recommendations per weakness so
# the UI doesn't get spammed.
_RECOMMENDED_BY_DIMENSION: dict[str, list[str]] = {
    "final_correctness": [
        # Missions that reward fixing the root cause vs the symptom.
        "agent-wrong-file",
        "overfitted-test-fix",
    ],
    "verification": [
        # Missions where running the right test surfaces the real failure.
        "async-race-condition",
        "missing-regression-test",
    ],
    "agent_review": [
        # Missions where the agent's narrative is plausible but the diff
        # is wrong — supervisor must actually read.
        "auth-cookie-expiration",
        "excessive-rewrite",
    ],
    "prompt_quality": [
        # Missions where a vague prompt produces a vague-correct fix and
        # a specific prompt produces the right one.
        "api-contract-drift",
        "typecheck-ignored",
    ],
    "context_selection": [
        # Missions where picking the wrong file set sends the agent down
        # the wrong path entirely.
        "agent-wrong-file",
        "api-contract-drift",
    ],
    "safety": [
        # Missions where the agent removes a security check entirely.
        "security-validation-removed",
        "dependency-misuse",
    ],
    "diff_minimality": [
        # Missions that reward the smallest-possible fix.
        "excessive-rewrite",
        "auth-cookie-expiration",
    ],
}

# Human-readable dimension labels for diagnostic text.
_DIM_LABEL: dict[str, str] = {
    "final_correctness": "Final patch correctness",
    "verification": "Verification discipline",
    "agent_review": "Agent output review",
    "prompt_quality": "Prompt quality",
    "context_selection": "Context selection",
    "safety": "Safety awareness",
    "diff_minimality": "Diff minimality",
}


@dataclass
class Diagnostic:
    """One actionable diagnosis. Matches the shared-types ``Diagnostic``
    interface used by the frontend report renderer."""

    dimension: str
    score: int | None
    max_score: int
    cause: str
    recommendation: str
    recommended_mission_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "score": self.score,
            "max": self.max_score,
            "cause": self.cause,
            "recommendation": self.recommendation,
            "recommended_mission_ids": self.recommended_mission_ids,
        }


# ---------------------------------------------------------------------------
# Per-dimension cause derivation. Each helper reads the raw signal strings
# produced by app.grading.score._score_* and picks the most actionable
# diagnostic. When no specific signal pattern matches, we fall back to a
# generic "you scored low on X" message that still names the dimension.
# ---------------------------------------------------------------------------


def _has_signal(signals: list[str], *needles: str) -> bool:
    return any(any(n in s for n in needles) for s in signals)


def _cause_final_correctness(signals: list[str]) -> str:
    if _has_signal(signals, "hidden tests did not pass", "0/"):
        # Look for the proportional hidden-test signal to be specific.
        for s in signals:
            if "hidden tests" in s and "passed" in s and "proportional" in s:
                return (
                    f"{s.strip()} — the hidden suite checks behaviours the "
                    "visible suite doesn't, so the diff is still missing "
                    "part of the root cause."
                )
        return (
            "Hidden tests did not pass — the diff fixed the visible symptom "
            "but missed at least one behaviour the hidden suite checks."
        )
    if _has_signal(signals, "root cause not clearly addressed"):
        return (
            "The submitted diff did not touch any of the mission's "
            "expected files. The agent's fix probably modified the wrong "
            "layer — re-read the brief, identify the root cause file, "
            "then ask the agent to fix it there."
        )
    return "Correctness signals were weak — see the per-signal breakdown above."


def _cause_verification(signals: list[str]) -> str:
    if _has_signal(signals, "no targeted test command found"):
        return (
            "You never ran the test command that exercises the mission's "
            "expected behaviour. Run it before you submit so you can see "
            "what the agent's patch actually does."
        )
    if _has_signal(signals, "no follow-up edit or prompt"):
        return (
            "You ran the targeted test and it failed, but you did not edit "
            "any file or push back on the agent before submitting. Read "
            "the test output and act on it."
        )
    if _has_signal(signals, "no typecheck run", "no lint run"):
        return (
            "You skipped the typecheck or lint step. Type and lint errors "
            "often surface the exact line the agent introduced a bug on."
        )
    return "Verification signals were weak — see the per-signal breakdown above."


def _cause_agent_review(signals: list[str]) -> str:
    if _has_signal(signals, "hard zero", "submit within 15s"):
        return (
            "You submitted within 15 seconds of the agent responding "
            "without opening the diff. There is no way you read what the "
            "agent did. Always open the diff after the agent applies a "
            "patch."
        )
    if _has_signal(signals, "open-and-close, partial credit"):
        return (
            "You opened the diff but spent less than 5 seconds on it. The "
            "agent's diff in this mission looks plausible but is wrong — "
            "spend real time reading it before you decide."
        )
    if _has_signal(signals, "diff not opened after the most recent patch"):
        return (
            "The most recent patch the agent applied was never reviewed "
            "in the diff panel. Any earlier review you did was of a stale "
            "patch."
        )
    if _has_signal(signals, "all were no-op"):
        return (
            "Your file edits all wrote zero changed lines — Monaco "
            "serialises saves even when the buffer hasn't changed. The "
            "grader requires at least one changed line to credit a "
            "meaningful edit."
        )
    if _has_signal(signals, "no revisionary prompt found"):
        return (
            "You did not push back on the agent with a revising prompt. "
            "Real supervision often requires asking the agent to refine "
            "or narrow its fix."
        )
    return "Agent-review signals were weak — see the per-signal breakdown above."


def _cause_prompt_quality(signals: list[str]) -> str:
    if _has_signal(signals, "prompt_quality_pending"):
        return (
            "The LLM judge was not available when this submission was "
            "graded, so the prompt-quality dimension is pending. It will "
            "be filled in automatically on the next grading run with a "
            "warm cache."
        )
    if _has_signal(signals, "via LLM judge"):
        for s in signals:
            if (
                "specificity=" in s
                and "constraint=" in s
                and "engagement=" in s
            ):
                # Surface the weakest axis as the diagnostic.
                weakest = _weakest_axis(s)
                if weakest:
                    return _AXIS_DIAGNOSTIC[weakest]
                return s.strip()
    if _has_signal(signals, "-3:", "-2:"):
        return (
            "Your prompts were short or vague ('fix it', 'make it work'). "
            "A strong supervision prompt names specific files or symbols, "
            "states a scope constraint, and defines a checkable success "
            "condition."
        )
    return "Prompt-quality signals were weak — see the per-signal breakdown above."


def _cause_context_selection(signals: list[str]) -> str:
    if _has_signal(signals, "discouraged"):
        for s in signals:
            if "discouraged_hit=" in s and "discouraged_hit=0" not in s:
                return (
                    "You included files in the agent's context that the "
                    "mission flagged as discouraged — they sent the agent "
                    "off-target. Be more selective."
                )
    if _has_signal(signals, "required_hit=0"):
        return (
            "You did not include any of the mission's required files in "
            "the agent's context. The agent had to guess what to read; "
            "give it the right files up front."
        )
    if _has_signal(signals, "no context.selected event found"):
        return (
            "You never picked any files for the agent's context. The "
            "agent only saw the brief. Open the file tree and add the "
            "files the agent should consider before prompting."
        )
    if _has_signal(signals, "no prompt.submitted yet"):
        return (
            "You explored multiple file selections but submitted without "
            "a corresponding prompt. The grader scored your most recent "
            "selection — make sure your final pick is the deliberate one."
        )
    return "Context-selection signals were weak — see the per-signal breakdown above."


def _cause_safety(signals: list[str]) -> str:
    if _has_signal(signals, "no corrective action after validator flag"):
        return (
            "A safety validator flagged the agent's diff and you did not "
            "edit or revert. The validators describe high-severity "
            "patterns — read them before submitting."
        )
    if _has_signal(signals, "new dependencies detected"):
        return (
            "The submitted diff adds a new third-party dependency. Most "
            "missions can be solved with stdlib only — adding a "
            "dependency to silence a problem is a code smell."
        )
    if _has_signal(signals, "banned command"):
        return (
            "You ran a command the mission flagged as banned (network or "
            "destructive). Stay inside the sandbox's allowed tooling."
        )
    return "Safety signals were weak — see the per-signal breakdown above."


def _cause_diff_minimality(signals: list[str]) -> str:
    if _has_signal(signals, "out of band"):
        return (
            "The mission's expected_diff_lines_p50 was out of band; the "
            "grader fell back to the default. Your real minimality is "
            "best read off the churn numbers in the signals."
        )
    if _has_signal(signals, "> 3.0"):
        return (
            "Your diff is more than 3× the size of the ideal fix. The "
            "agent probably did an excessive rewrite — push back and ask "
            "it to make the smallest change that fixes the bug."
        )
    return "Diff-minimality signals were weak — see the per-signal breakdown above."


_DIAGNOSE_BY_DIMENSION = {
    "final_correctness": _cause_final_correctness,
    "verification": _cause_verification,
    "agent_review": _cause_agent_review,
    "prompt_quality": _cause_prompt_quality,
    "context_selection": _cause_context_selection,
    "safety": _cause_safety,
    "diff_minimality": _cause_diff_minimality,
}


# ---------------------------------------------------------------------------
# Per-axis cause helpers for the LLM-judge prompt-quality dimension.
# ---------------------------------------------------------------------------


_AXIS_DIAGNOSTIC: dict[str, str] = {
    "specificity": (
        "Your prompts were too generic. Name specific files, functions, "
        "or symbols you want the agent to focus on — don't just describe "
        "the bug."
    ),
    "constraint": (
        "Your prompts did not state scope constraints. Tell the agent "
        "what NOT to change ('do not modify the schema', 'minimal diff "
        "only') so it doesn't go beyond the fix."
    ),
    "engagement": (
        "Your follow-up prompts did not respond to specific points the "
        "agent made. When the agent explains its approach, push back on "
        "the part you disagree with — don't just say 'try again'."
    ),
    "verifiability": (
        "Your prompts did not define a checkable success condition. Tell "
        "the agent what the test or behaviour should show ('the regression "
        "test should pass', 'no `any` casts in the diff') so success is "
        "objective."
    ),
}


def _weakest_axis(signal: str) -> str | None:
    """Parse a judge signal like
    ``judge[fresh] score=4/10 specificity=0.5/2.5 constraint=0.5/2.5 ...``
    and return the lowest-scoring axis name."""
    parts: dict[str, float] = {}
    for token in signal.split():
        for axis in ("specificity", "constraint", "engagement", "verifiability"):
            prefix = axis + "="
            if token.startswith(prefix):
                try:
                    parts[axis] = float(token[len(prefix):].split("/")[0])
                except ValueError:
                    pass
    if not parts:
        return None
    return min(parts, key=lambda k: parts[k])


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def build_feedback_narrative(
    *,
    dimensions: dict[str, Any],
    completed_mission_ids: list[str] | None = None,
    max_diagnostics: int = 4,
) -> list[Diagnostic]:
    """Produce one ``Diagnostic`` per weakness, ordered by severity.

    A "weakness" is a dimension whose score is at or below 50% of its
    max. Pending dimensions (``score = -1`` sentinel) are included
    because the user benefits from being told their measurement was
    incomplete. We return at most ``max_diagnostics`` so the UI stays
    scannable.

    Recommended mission ids exclude anything already in
    ``completed_mission_ids`` — there's no point sending the user back
    to a mission they have already attempted.
    """
    completed = set(completed_mission_ids or [])

    # Score each dimension's "weakness intensity" — pending dimensions are
    # surfaced too (severity = max, so they sort high). Among scored
    # dimensions, the relative gap from 50% drives the priority.
    scored: list[tuple[float, str, Any]] = []
    for dim_name, ds in dimensions.items():
        max_s = getattr(ds, "max_score", 0)
        if max_s <= 0:
            continue
        if getattr(ds, "pending", False):
            # Pending dimensions: insert at a high but not maximal
            # severity so a true zero still beats a pending.
            scored.append((0.9, dim_name, ds))
            continue
        score = max(0, int(getattr(ds, "score", 0)))
        ratio = score / max_s
        if ratio > 0.5:
            continue  # not a weakness
        # Severity = 1 - ratio (so 0 -> 1.0, 0.5 -> 0.5).
        scored.append((1.0 - ratio, dim_name, ds))

    scored.sort(key=lambda t: t[0], reverse=True)
    out: list[Diagnostic] = []
    for _, dim_name, ds in scored[:max_diagnostics]:
        cause_fn = _DIAGNOSE_BY_DIMENSION.get(dim_name)
        cause = (
            cause_fn(getattr(ds, "signals", []) or [])
            if cause_fn is not None
            else "See per-signal breakdown above."
        )
        recs = [
            mid
            for mid in _RECOMMENDED_BY_DIMENSION.get(dim_name, [])
            if mid not in completed
        ][:2]
        if recs:
            recommendation = _format_recommendation(dim_name, recs)
        else:
            recommendation = (
                f"You have already attempted every mission designed to "
                f"exercise {_DIM_LABEL.get(dim_name, dim_name)}. Try "
                f"re-running one of them with a deliberate focus on this "
                f"dimension."
            )
        out.append(
            Diagnostic(
                dimension=dim_name,
                score=None if getattr(ds, "pending", False) else int(ds.score),
                max_score=int(ds.max_score),
                cause=cause,
                recommendation=recommendation,
                recommended_mission_ids=recs,
            )
        )
    return out


def _format_recommendation(dim_name: str, mission_ids: list[str]) -> str:
    label = _DIM_LABEL.get(dim_name, dim_name)
    if len(mission_ids) == 1:
        return (
            f"Try {mission_ids[0]} next — it directly exercises "
            f"{label.lower()}."
        )
    joined = " and ".join(mission_ids)
    return (
        f"Try {joined} next — both directly exercise "
        f"{label.lower()}."
    )
