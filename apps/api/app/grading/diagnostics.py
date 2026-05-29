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
            if "specificity=" in s and "constraint=" in s and "engagement=" in s:
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
            "Your diff is more than 3x the size of the ideal fix. The "
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
                    parts[axis] = float(token[len(prefix) :].split("/")[0])
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
    engine_recommended_mission_ids: list[str] | None = None,
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

    P1-2 — when ``engine_recommended_mission_ids`` is supplied, it
    overrides the legacy per-dimension static lists. The persisted
    field becomes a globally-ranked top-3 (same on every diagnostic
    entry) sourced from the deterministic recommendation engine. The
    P0-11 verify envelope hashes against the persisted ranking, so
    this must remain deterministic for the same user + history.
    """
    completed = set(completed_mission_ids or [])
    engine_recs: list[str] | None = (
        list(engine_recommended_mission_ids) if engine_recommended_mission_ids is not None else None
    )

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
        # P1-2 — when the engine has supplied the globally-ranked top-3
        # ids, use them directly instead of the legacy per-dimension
        # static list. The same ranking is surfaced on every diagnostic
        # entry so the persisted field carries the same top-3 the
        # ``/me/recommendations`` endpoint serves on the hot path.
        if engine_recs is not None:
            recs = list(engine_recs)
        else:
            recs = [
                mid for mid in _RECOMMENDED_BY_DIMENSION.get(dim_name, []) if mid not in completed
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
        return f"Try {mission_ids[0]} next — it directly exercises {label.lower()}."
    joined = " and ".join(mission_ids)
    return f"Try {joined} next — both directly exercise {label.lower()}."


# ---------------------------------------------------------------------------
# P0-2 — Critical-moment heuristic.
# ---------------------------------------------------------------------------

# Critical-moment kinds — narrow set; the FE renders a specific template per
# kind so adding a new one requires a FE update too.
CriticalMomentKind = str  # Literal["agent_responded_no_review",
#                                    "submitted_without_verification",
#                                    "wrong_layer_committed",
#                                    "missed_corrective_window"]


@dataclass(slots=True)
class CriticalMoment:
    """One deterministic "you went off course here" callout for the
    post-mortem walkthrough.

    Each entry pins to a specific supervision event id (so the FE can scroll
    the timeline) and carries human-readable copy templated per ``kind``.
    Severity is monotonic — higher means more painful in the report.
    """

    event_id: int
    kind: str
    explanation: str
    what_to_do_instead: str
    severity: int = 1
    occurred_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "explanation": self.explanation,
            "what_to_do_instead": self.what_to_do_instead,
            "severity": self.severity,
            "occurred_at": self.occurred_at,
        }


# Per-kind copy. Keyed by ``kind``; the FE renders ``explanation`` as the
# headline and ``what_to_do_instead`` as the actionable follow-up.
_CRITICAL_MOMENT_COPY: dict[str, tuple[str, str, int]] = {
    "agent_responded_no_review": (
        "The agent applied a patch after this response and you submitted "
        "without opening the diff. You can't review what you didn't read.",
        "Open the diff after every patch.applied and search for the bug's "
        "key term before pressing Submit.",
        4,
    ),
    "submitted_without_verification": (
        "You submitted without running a single test or typecheck command. "
        "Verification is the highest-leverage supervisor behaviour.",
        "Run the targeted test suite (or `pnpm typecheck`) at least once "
        "between the agent's patch and your submission.",
        5,
    ),
    "wrong_layer_committed": (
        "A forbidden-changes validator flagged the agent's patch and you "
        "submitted it anyway without reverting the offending file.",
        "When a validator flags a file, revert the change (the workspace "
        "ships a revert affordance on the file tree) before pressing Submit.",
        4,
    ),
    "missed_corrective_window": (
        "You submitted within 15 seconds of the agent's last response. That "
        "is below the time it takes to read a diff carefully.",
        "Pause after the agent responds — read the narration AND the diff "
        "before deciding to submit. 30 seconds is a reasonable floor.",
        3,
    ),
    # P0-12 — anchored to the second ``session.reset`` event when a
    # supervisor backtracks twice. One reset is exploration; two is a
    # pattern the post-mortem should call out.
    "reset_then_repeated_same_mistake": (
        "You reset the workspace at least twice. A second backtrack "
        "usually means the supervisor is iterating on the same wrong "
        "hypothesis rather than re-reading the failure mode.",
        "After a reset, try a different angle: re-read the mission brief, "
        "search the codebase for the failure-mode keyword, or ask the "
        "agent to first explain its mental model of the bug before "
        "writing any more code.",
        4,
    ),
}


def _event_id(event: dict[str, Any]) -> int | None:
    """Return the event's persistent id, or None if absent.

    The grading runner's ``_load_events`` strips the id off (it only
    serialises ``event_type``/``payload``/``occurred_at``), so callers
    that want critical moments must re-pull with the id intact. We
    fall back gracefully (the moment is suppressed) when an id is
    missing rather than raising.
    """
    raw = event.get("id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _index_of_first(events: list[dict[str, Any]], event_type: str) -> int | None:
    for i, ev in enumerate(events):
        if ev.get("event_type") == event_type:
            return i
    return None


def _index_of_last(events: list[dict[str, Any]], event_type: str) -> int | None:
    last: int | None = None
    for i, ev in enumerate(events):
        if ev.get("event_type") == event_type:
            last = i
    return last


def _parse_iso(value: Any) -> Any:
    """Best-effort ISO-8601 → datetime. Returns None on failure.

    Logs at warning level when a string IS provided but doesn't parse so
    an operator can spot malformed event timestamps that would otherwise
    silently suppress a ``missed_corrective_window`` moment.
    """
    from datetime import datetime as _dt

    from loguru import logger

    if value is None:
        return None
    if isinstance(value, str):
        try:
            return _dt.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "[critical_moments] could not parse occurred_at {!r} — "
                "moment derivation will skip this event",
                value,
            )
            return None
    return None


def compute_critical_moments(  # noqa: PLR0912, PLR0915 — four heuristics are inherently branchy
    *,
    events: list[dict[str, Any]],
    manifest: Any = None,
    max_moments: int = 3,
) -> list[CriticalMoment]:
    """Return up to ``max_moments`` critical moments for the session.

    Pure function over the event log. The four kinds the heuristic
    detects mirror the design doc:

      * ``agent_responded_no_review`` — last ``agent.responded`` whose
        following ``patch.applied`` was never reviewed via
        ``diff.opened`` before submission.
      * ``submitted_without_verification`` — no test / typecheck command
        ever ran in this session.
      * ``wrong_layer_committed`` — a ``validator.flag`` (kind:
        ``forbidden_changes``) fired and no ``file.reverted`` followed
        for the same path.
      * ``missed_corrective_window`` — ``submission.requested`` occurred
        within 15 seconds of the most recent ``agent.responded``.

    Each moment maps to an exact event id so the FE can scroll the
    timeline. Moments are sorted by severity (desc) then by event order;
    duplicates on the same event id are coalesced. Empty list when none
    of the heuristics trip.
    """
    out: list[CriticalMoment] = []
    if not events:
        return out

    submit_idx = _index_of_first(events, "submission.requested")
    # If the user never submitted, every heuristic loses its anchor; the
    # report still renders without moments.
    events_before_submit = events if submit_idx is None else events[:submit_idx]

    # ─── agent_responded_no_review ─────────────────────────────────────────
    # Walk forward: for each agent.responded, find the *immediately
    # following* patch.applied; if no diff.opened sits between that
    # patch.applied and submission.requested (or end-of-events), surface
    # the agent.responded as the moment.
    last_responded_no_review: tuple[int, int] | None = None  # (event_index, event_id)
    for i, ev in enumerate(events_before_submit):
        if ev.get("event_type") != "agent.responded":
            continue
        patch_idx: int | None = None
        for j in range(i + 1, len(events_before_submit)):
            if events_before_submit[j].get("event_type") == "patch.applied":
                patch_idx = j
                break
        if patch_idx is None:
            continue
        reviewed = False
        for k in range(patch_idx + 1, len(events_before_submit)):
            if events_before_submit[k].get("event_type") == "diff.opened":
                reviewed = True
                break
        if reviewed:
            continue
        eid = _event_id(ev)
        if eid is None:
            continue
        last_responded_no_review = (i, eid)
    if last_responded_no_review is not None:
        idx, eid = last_responded_no_review
        explanation, what_to_do, severity = _CRITICAL_MOMENT_COPY["agent_responded_no_review"]
        out.append(
            CriticalMoment(
                event_id=eid,
                kind="agent_responded_no_review",
                explanation=explanation,
                what_to_do_instead=what_to_do,
                severity=severity,
                occurred_at=events_before_submit[idx].get("occurred_at"),
            )
        )

    # ─── submitted_without_verification ────────────────────────────────────
    test_categories = {"test", "typecheck"}
    has_verification = False
    for ev in events_before_submit:
        if ev.get("event_type") != "command.run":
            continue
        payload = ev.get("payload") or {}
        category = payload.get("category")
        if category in test_categories:
            has_verification = True
            break
    if not has_verification:
        first_prompt_idx = _index_of_first(events_before_submit, "prompt.submitted")
        if first_prompt_idx is not None:
            ev = events_before_submit[first_prompt_idx]
            eid = _event_id(ev)
            if eid is not None:
                explanation, what_to_do, severity = _CRITICAL_MOMENT_COPY[
                    "submitted_without_verification"
                ]
                out.append(
                    CriticalMoment(
                        event_id=eid,
                        kind="submitted_without_verification",
                        explanation=explanation,
                        what_to_do_instead=what_to_do,
                        severity=severity,
                        occurred_at=ev.get("occurred_at"),
                    )
                )

    # ─── wrong_layer_committed ─────────────────────────────────────────────
    # A validator.flag with kind=forbidden_changes that was never followed
    # by a file.reverted on the same path before submit. Anchor to the
    # patch.applied that introduced the change (the validator flag itself
    # is the report-time event; the user's decision moment was the patch).
    flagged_path: str | None = None
    for ev in events_before_submit:
        if ev.get("event_type") != "validator.flag":
            continue
        payload = ev.get("payload") or {}
        if payload.get("kind") != "forbidden_changes":
            continue
        # Look for matching file.reverted later in the stream.
        path = payload.get("file") or payload.get("path")
        reverted = False
        for later in events_before_submit:
            if later is ev:
                continue
            if later.get("event_type") != "file.reverted":
                continue
            later_path = (later.get("payload") or {}).get("path")
            if not path or later_path == path:
                reverted = True
                break
        if reverted:
            continue
        flagged_path = path
        last_patch_idx = _index_of_last(events_before_submit, "patch.applied")
        if last_patch_idx is None:
            continue
        anchor_ev = events_before_submit[last_patch_idx]
        eid = _event_id(anchor_ev)
        if eid is None:
            continue
        explanation, what_to_do, severity = _CRITICAL_MOMENT_COPY["wrong_layer_committed"]
        if flagged_path:
            explanation = (
                f"The patch touched `{flagged_path}` which is on this mission's "
                "forbidden-changes list, and you submitted without reverting it."
            )
        out.append(
            CriticalMoment(
                event_id=eid,
                kind="wrong_layer_committed",
                explanation=explanation,
                what_to_do_instead=what_to_do,
                severity=severity,
                occurred_at=anchor_ev.get("occurred_at"),
            )
        )
        # Continue scanning: a session that flagged forbidden_changes on
        # multiple files yields one moment per anchor (deduped below by
        # ``(event_id, kind)`` so a repeated anchor still collapses).
        # The previous ``break`` silently lost subsequent violations.

    # ─── missed_corrective_window ──────────────────────────────────────────
    if submit_idx is not None:
        submit_ev = events[submit_idx]
        submit_ts = _parse_iso(submit_ev.get("occurred_at"))
        last_responded_idx = _index_of_last(events_before_submit, "agent.responded")
        if last_responded_idx is not None and submit_ts is not None:
            responded_ev = events_before_submit[last_responded_idx]
            responded_ts = _parse_iso(responded_ev.get("occurred_at"))
            if responded_ts is not None:
                delta = (submit_ts - responded_ts).total_seconds()
                if 0 <= delta < 15:
                    eid = _event_id(responded_ev)
                    if eid is not None:
                        explanation, what_to_do, severity = _CRITICAL_MOMENT_COPY[
                            "missed_corrective_window"
                        ]
                        out.append(
                            CriticalMoment(
                                event_id=eid,
                                kind="missed_corrective_window",
                                explanation=explanation,
                                what_to_do_instead=what_to_do,
                                severity=severity,
                                occurred_at=responded_ev.get("occurred_at"),
                            )
                        )

    # ─── reset_then_repeated_same_mistake (P0-12) ────────────────────────
    # Two ``session.reset`` events on the same session — anchor to the
    # second one so the timeline scrubber lands on the moment the
    # backtrack pattern became visible. Walk the full event stream (not
    # just events_before_submit) so a reset → abandon flow still
    # surfaces; reset is the load-bearing signal here, not the submit.
    reset_event_ids: list[int] = []
    reset_occurred_at: list[str | None] = []
    for ev in events:
        if ev.get("event_type") != "session.reset":
            continue
        eid = _event_id(ev)
        if eid is None:
            continue
        reset_event_ids.append(eid)
        reset_occurred_at.append(ev.get("occurred_at"))
    if len(reset_event_ids) >= 2:
        explanation, what_to_do, severity = _CRITICAL_MOMENT_COPY[
            "reset_then_repeated_same_mistake"
        ]
        out.append(
            CriticalMoment(
                event_id=reset_event_ids[1],
                kind="reset_then_repeated_same_mistake",
                explanation=explanation,
                what_to_do_instead=what_to_do,
                severity=severity,
                occurred_at=reset_occurred_at[1],
            )
        )

    # Dedupe by (event_id, kind) — coalesce duplicates that show up across
    # the heuristics. Stable order by severity desc, then event_id asc.
    seen: set[tuple[int, str]] = set()
    deduped: list[CriticalMoment] = []
    for cm in out:
        key = (cm.event_id, cm.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cm)
    deduped.sort(key=lambda cm: (-cm.severity, cm.event_id))
    return deduped[:max_moments]


__all__ = [
    "RECOMMENDATION_VERSION",
    "CriticalMoment",
    "Diagnostic",
    "build_feedback_narrative",
    "compute_critical_moments",
]
