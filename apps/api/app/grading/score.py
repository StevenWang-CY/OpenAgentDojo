"""Grading scoring engine — §11.2 seven-dimension rubric.

All functions are pure (no DB, no I/O, no randomness) and fully deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult
from app.grading.validators.tests_pass import TestRunResult


@dataclass
class DimensionScore:
    score: int
    max_score: int
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "max_score": self.max_score,
            "signals": self.signals,
        }


@dataclass
class ScoreReport:
    total: int
    dimensions: dict[str, DimensionScore]
    strengths: list[str]
    weaknesses: list[str]
    missed_failure_mode: bool
    badges_earned: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "missed_failure_mode": self.missed_failure_mode,
            "badges_earned": self.badges_earned,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_validator(results: list[ValidatorResult], kind: str) -> ValidatorResult | None:
    for r in results:
        if r.kind == kind:
            return r
    return None


def _events_of_type(events: list[dict[str, Any]], *types: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("event_type") in types]


def _any_event(events: list[dict[str, Any]], *types: str) -> bool:
    return any(e.get("event_type") in types for e in events)


def _event_occurred_before(
    events: list[dict[str, Any]],
    before_type: str,
    target_type: str,
) -> bool:
    """Return True if a ``target_type`` event exists before any ``before_type`` event."""
    before_idx: int | None = None
    target_idx: int | None = None
    for i, e in enumerate(events):
        et = e.get("event_type")
        if et == before_type and before_idx is None:
            before_idx = i
        if et == target_type and target_idx is None:
            target_idx = i

    if before_idx is None or target_idx is None:
        return False
    return target_idx < before_idx


def _event_occurred_after(
    events: list[dict[str, Any]],
    after_type: str,
    target_type: str,
) -> bool:
    """Return True if a ``target_type`` event exists anywhere after ``after_type`` event."""
    after_idx: int | None = None
    for i, e in enumerate(events):
        et = e.get("event_type")
        if et == after_type and after_idx is None:
            after_idx = i
        if et == target_type and after_idx is not None and i > after_idx:
            return True
    return False


def _hidden_tests_passed(test_results: list[TestRunResult]) -> bool:
    hidden = [r for r in test_results if "hidden" in r.suite.lower()]
    if not hidden:
        return False  # No hidden tests = treat as failed for scoring
    return all(r.exit_code == 0 for r in hidden)


def _visible_tests_passed(test_results: list[TestRunResult]) -> bool:
    """Return True iff every visible *unit/integration* suite exited 0.

    Lint and typecheck are excluded from this gate — they have their own
    verification credit (§11.2.2) and a flaky lint config should not
    zero-out the correctness dimension when the tests themselves are green.
    """
    visible = [
        r
        for r in test_results
        if "hidden" not in r.suite.lower() and r.suite.lower() not in {"lint", "typecheck"}
    ]
    if not visible:
        return False
    return all(r.exit_code == 0 for r in visible)


def _suite_by_name_contains(
    test_results: list[TestRunResult], fragment: str
) -> list[TestRunResult]:
    return [r for r in test_results if fragment.lower() in r.suite.lower()]


# ---------------------------------------------------------------------------
# Dimension 1: Final Patch Correctness (max 30)
# ---------------------------------------------------------------------------


def _score_final_correctness(
    diff: ParsedDiff,
    validator_results: list[ValidatorResult],
    test_results: list[TestRunResult],
    manifest: Any,
) -> DimensionScore:
    score = 0
    signals: list[str] = []

    hidden_pass = _hidden_tests_passed(test_results)
    visible_pass = _visible_tests_passed(test_results)

    # Regression proxy: existing tests (visible) still pass after patch.
    regression_ok = visible_pass

    # Root-cause proxy: regression_test_required validator passed,
    # OR at least one of the expected_files was touched.
    regression_val = _find_validator(validator_results, "regression_test_required")
    expected_files: list[str] = list(getattr(manifest, "expected_files", []))
    root_cause_addressed = (regression_val is not None and regression_val.passed) or any(
        p in diff.changed_paths() for p in expected_files
    )

    if hidden_pass:
        score += 12
        signals.append("+12: all hidden tests pass")
    else:
        signals.append("+0: hidden tests did not pass")

    if visible_pass:
        score += 8
        signals.append("+8: all visible tests pass")
    else:
        signals.append("+0: visible tests did not pass")

    if regression_ok:
        score += 6
        signals.append("+6: no regression in existing tests")
    else:
        signals.append("+0: existing tests regressed")

    if root_cause_addressed:
        score += 4
        signals.append("+4: root cause addressed (regression test or expected files touched)")
    else:
        signals.append("+0: root cause not clearly addressed")

    # Floor: cap at 18 if hidden tests fail.
    if not hidden_pass and score > 18:
        signals.append(f"capped at 18 (hidden tests failed, was {score})")
        score = 18

    return DimensionScore(score=min(score, 30), max_score=30, signals=signals)


# ---------------------------------------------------------------------------
# Dimension 2: Verification Discipline (max 20)
# ---------------------------------------------------------------------------


def _score_verification(
    events: list[dict[str, Any]],
    validator_results: list[ValidatorResult],
    manifest: Any,
) -> DimensionScore:
    score = 0
    signals: list[str] = []

    command_run_events = _events_of_type(events, "command.run")
    has_any_command = len(command_run_events) > 0

    # +8: test command run that matches require_targeted_test.
    targeted_test_pattern: str | None = None
    try:
        targeted_test_pattern = manifest.reward_signals.verification.require_targeted_test
    except AttributeError:
        pass

    has_targeted_test = False
    for e in command_run_events:
        payload = e.get("payload", {})
        category = payload.get("category", "")
        command = payload.get("command", "")
        if category == "test":
            if targeted_test_pattern is None or re.search(
                targeted_test_pattern, command, re.IGNORECASE
            ):
                has_targeted_test = True
                break

    if has_targeted_test:
        score += 8
        signals.append("+8: targeted test command run")
    else:
        signals.append("+0: no targeted test command found")

    # +4: typecheck command run.
    has_typecheck = any(
        e.get("payload", {}).get("category") == "typecheck" for e in command_run_events
    )
    if has_typecheck:
        score += 4
        signals.append("+4: typecheck run")
    else:
        signals.append("+0: no typecheck run")

    # +3: lint command run.
    has_lint = any(e.get("payload", {}).get("category") == "lint" for e in command_run_events)
    if has_lint:
        score += 3
        signals.append("+3: lint run")
    else:
        signals.append("+0: no lint run")

    # +5: regression_test_required validator passed.
    regression_val = _find_validator(validator_results, "regression_test_required")
    if regression_val is not None and regression_val.passed:
        score += 5
        signals.append("+5: regression test validator passed")
    else:
        signals.append("+0: regression test validator not passed")

    # -8: zero command.run events of any kind.
    if not has_any_command:
        score -= 8
        signals.append("-8: no command.run events at all")

    return DimensionScore(score=max(0, min(score, 20)), max_score=20, signals=signals)


# ---------------------------------------------------------------------------
# Dimension 3: Agent Output Review (max 15)
# ---------------------------------------------------------------------------


def _score_agent_review(
    events: list[dict[str, Any]],
    manifest: Any,
) -> DimensionScore:
    score = 0
    signals: list[str] = []

    # Hard-zero check: submission within 15s of agent.responded AND no diff opened.
    submission_events = _events_of_type(events, "submission.requested")
    agent_responded_events = _events_of_type(events, "agent.responded")
    diff_opened_events = _events_of_type(events, "diff.opened")

    rushed_submit = False
    if submission_events and agent_responded_events:
        last_agent = agent_responded_events[-1]
        first_submit = submission_events[0]
        agent_ts = last_agent.get("occurred_at") or last_agent.get("payload", {}).get("occurred_at")
        submit_ts = first_submit.get("occurred_at") or first_submit.get("payload", {}).get(
            "occurred_at"
        )
        if agent_ts and submit_ts:
            # occurred_at may be a string (ISO 8601) or a number (epoch seconds).
            try:
                if isinstance(agent_ts, str):
                    # Py 3.11+ handles trailing "Z" via fromisoformat.
                    agent_dt = datetime.fromisoformat(agent_ts.replace("Z", "+00:00"))
                    submit_dt = datetime.fromisoformat(submit_ts.replace("Z", "+00:00"))
                    delta = (submit_dt - agent_dt).total_seconds()
                else:
                    delta = float(submit_ts) - float(agent_ts)
                if delta <= 15 and not diff_opened_events:
                    rushed_submit = True
            except (ValueError, TypeError):
                pass

    if rushed_submit:
        signals.append("0/15: submit within 15s of agent.responded with no diff opened (hard zero)")
        return DimensionScore(score=0, max_score=15, signals=signals)

    # +6: diff.opened event occurred after patch.applied event.
    diff_after_patch = _event_occurred_after(events, "patch.applied", "diff.opened")
    if diff_after_patch:
        score += 6
        signals.append("+6: diff opened after patch applied")
    else:
        signals.append("+0: diff not opened after patch applied")

    # +5: any file.edited or file.reverted event.
    has_edit_or_revert = _any_event(events, "file.edited", "file.reverted")
    if has_edit_or_revert:
        score += 5
        signals.append("+5: file edited or reverted")
    else:
        signals.append("+0: no file edited/reverted")

    # +4: any prompt.submitted event with intent=revise, narrow, or test.
    revise_intents = {"revise", "narrow", "test"}
    has_revise_prompt = False
    for e in _events_of_type(events, "prompt.submitted"):
        payload = e.get("payload", {})
        intent = payload.get("intent", "")
        keyword_hits = payload.get("keyword_hits", [])
        prompt_text = payload.get("prompt", "").lower()

        if intent in revise_intents:
            has_revise_prompt = True
            break
        if any(kw in revise_intents for kw in (keyword_hits or [])):
            has_revise_prompt = True
            break
        # Classify from text if no intent field.
        if any(word in prompt_text for word in ("revise", "narrow", "test this", "run test")):
            has_revise_prompt = True
            break

    if has_revise_prompt:
        score += 4
        signals.append("+4: revisionary prompt submitted")
    else:
        signals.append("+0: no revisionary prompt found")

    return DimensionScore(score=min(score, 15), max_score=15, signals=signals)


# ---------------------------------------------------------------------------
# Dimension 4: Prompt Quality (max 10)
# ---------------------------------------------------------------------------


def _score_prompt_quality(
    agent_turns: list[dict[str, Any]],
    manifest: Any,
) -> DimensionScore:
    signals: list[str] = []

    must_include: list[str] = []
    bonus_keywords: list[str] = []
    try:
        must_include = list(manifest.reward_signals.prompt_quality.must_include_any)
        bonus_keywords = list(manifest.reward_signals.prompt_quality.bonus_keywords)
    except AttributeError:
        pass

    best_score = 0

    for turn in agent_turns:
        prompt = ""
        if isinstance(turn, dict):
            prompt = turn.get("user_prompt", turn.get("prompt", "")) or ""
        if not prompt:
            continue

        prompt_lower = prompt.lower()
        turn_score = 0
        turn_signals: list[str] = []

        # +2 if >= 80 chars.
        if len(prompt) >= 80:
            turn_score += 2
            turn_signals.append("+2: prompt >= 80 chars")

        # +2 if any must_include_any keyword present.
        if any(kw.lower() in prompt_lower for kw in must_include):
            turn_score += 2
            turn_signals.append("+2: required keyword present")

        # +1 per bonus keyword (max +3).
        bonus_hit = sum(1 for kw in bonus_keywords if kw.lower() in prompt_lower)
        bonus_pts = min(bonus_hit, 3)
        if bonus_pts:
            turn_score += bonus_pts
            turn_signals.append(f"+{bonus_pts}: {bonus_pts} bonus keyword(s)")

        # +2 if "test" or "regression" in prompt.
        if "test" in prompt_lower or "regression" in prompt_lower:
            turn_score += 2
            turn_signals.append("+2: test/regression mentioned")

        # +2 if scope phrase present.
        scope_phrases = ["do not modify", "minimal", "without changing", "only change"]
        if any(p in prompt_lower for p in scope_phrases):
            turn_score += 2
            turn_signals.append("+2: scope phrase present")

        # -3 if prompt < 40 chars.
        if len(prompt) < 40:
            turn_score -= 3
            turn_signals.append("-3: prompt < 40 chars")

        # -2 if prompt is vague-only.
        vague_phrases = ["fix it", "make it work", "just fix", "do it"]
        prompt_stripped = prompt.strip().lower()
        if any(prompt_stripped == vague for vague in vague_phrases) or (
            all(vague in prompt_stripped for vague in ["fix"]) and len(prompt) < 20
        ):
            turn_score -= 2
            turn_signals.append("-2: vague-only prompt")

        turn_score = max(0, min(turn_score, 10))
        if turn_score > best_score:
            best_score = turn_score
            signals = turn_signals

    return DimensionScore(score=max(0, min(best_score, 10)), max_score=10, signals=signals)


# ---------------------------------------------------------------------------
# Dimension 5: Context Selection (max 10)
# ---------------------------------------------------------------------------


def _score_context_selection(
    events: list[dict[str, Any]],
    manifest: Any,
) -> DimensionScore:
    signals: list[str] = []

    required: list[str] = []
    recommended: list[str] = []
    discouraged: list[str] = []
    try:
        required = list(manifest.expected_context.required)
        recommended = list(manifest.expected_context.recommended)
        discouraged = list(manifest.expected_context.discouraged)
    except AttributeError:
        pass

    # Get selected files from the last context.selected event.
    selected_files: list[str] = []
    for e in reversed(events):
        if e.get("event_type") == "context.selected":
            payload = e.get("payload", {})
            selected_files = list(payload.get("files", []))
            break

    selected_set = set(selected_files)
    required_set = set(required)
    recommended_set = set(recommended)
    discouraged_set = set(discouraged)

    required_hit = len(selected_set & required_set) / len(required_set) if required_set else 0.0
    recommended_hit = len(selected_set & recommended_set) / max(1, len(recommended_set))
    discouraged_hit = len(selected_set & discouraged_set)

    raw_score = round(required_hit * 7 + recommended_hit * 3) - min(3, discouraged_hit)
    score = max(0, min(raw_score, 10))

    signals.append(
        f"required_hit={required_hit:.2f} recommended_hit={recommended_hit:.2f} "
        f"discouraged_hit={discouraged_hit}"
    )
    if selected_files:
        signals.append(f"selected {len(selected_files)} file(s)")
    else:
        signals.append("no context.selected event found")

    return DimensionScore(score=score, max_score=10, signals=signals)


# ---------------------------------------------------------------------------
# Dimension 6: Safety Awareness (max 10)
# ---------------------------------------------------------------------------


def _score_safety(
    events: list[dict[str, Any]],
    validator_results: list[ValidatorResult],
    manifest: Any,
) -> DimensionScore:
    score = 0
    signals: list[str] = []

    # +4 if no forbidden_changes triggered.
    forbidden_val = _find_validator(validator_results, "forbidden_changes")
    if forbidden_val is not None and forbidden_val.passed:
        score += 4
        signals.append("+4: no forbidden changes")
    else:
        signals.append("+0: forbidden changes detected")

    # +3 if user reverted/rewrote a forbidden change (file.reverted or file.edited
    # after a validator.flag event for that file).
    validator_flag_events = _events_of_type(events, "validator.flag")
    reverted_after_flag = False
    for flag_evt in validator_flag_events:
        flag_idx = events.index(flag_evt)
        for i, e in enumerate(events):
            if i > flag_idx and e.get("event_type") in ("file.reverted", "file.edited"):
                reverted_after_flag = True
                break
        if reverted_after_flag:
            break

    if reverted_after_flag:
        score += 3
        signals.append("+3: user corrected flagged change")
    else:
        signals.append("+0: no corrective action after validator flag")

    # +2 if no_new_deps validator passed.
    deps_val = _find_validator(validator_results, "no_new_dependencies")
    if deps_val is not None and deps_val.passed:
        score += 2
        signals.append("+2: no new dependencies")
    else:
        signals.append("+0: new dependencies detected (or validator absent)")

    # +1 if no banned commands run.
    banned_commands: list[str] = []
    try:
        banned_commands = list(manifest.reward_signals.safety.must_not_run_commands)
    except AttributeError:
        pass

    command_run_events = _events_of_type(events, "command.run")
    has_banned = False
    for e in command_run_events:
        cmd = e.get("payload", {}).get("command", "")
        if any(banned in cmd for banned in banned_commands):
            has_banned = True
            break

    if not has_banned:
        score += 1
        signals.append("+1: no banned commands run")
    else:
        signals.append("+0: banned command was run")

    return DimensionScore(score=min(score, 10), max_score=10, signals=signals)


# ---------------------------------------------------------------------------
# Dimension 7: Diff Minimality (max 5)
# ---------------------------------------------------------------------------


def _score_diff_minimality(diff: ParsedDiff, manifest: Any) -> DimensionScore:
    signals: list[str] = []

    p50 = getattr(manifest, "expected_diff_lines_p50", 20)
    added = diff.added_lines_total()
    ratio = added / max(1, p50)

    if ratio <= 1.0:
        score = 5
        signals.append(f"ratio={ratio:.2f} <= 1.0 → 5/5")
    elif ratio <= 1.5:
        score = 4
        signals.append(f"ratio={ratio:.2f} in (1.0, 1.5] → 4/5")
    elif ratio <= 2.0:
        score = 3
        signals.append(f"ratio={ratio:.2f} in (1.5, 2.0] → 3/5")
    elif ratio <= 3.0:
        score = 2
        signals.append(f"ratio={ratio:.2f} in (2.0, 3.0] → 2/5")
    else:
        score = 0
        signals.append(f"ratio={ratio:.2f} > 3.0 → 0/5")

    signals.append(f"added_lines={added} p50={p50}")
    return DimensionScore(score=score, max_score=5, signals=signals)


# ---------------------------------------------------------------------------
# Strengths / Weaknesses narrative
# ---------------------------------------------------------------------------


def _narrative(dimensions: dict[str, DimensionScore]) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []

    thresholds = {
        "final_correctness": (24, 18),
        "verification": (16, 8),
        "agent_review": (12, 5),
        "prompt_quality": (8, 4),
        "context_selection": (8, 4),
        "safety": (8, 4),
        "diff_minimality": (4, 2),
    }
    labels = {
        "final_correctness": "Correctness",
        "verification": "Verification discipline",
        "agent_review": "Agent output review",
        "prompt_quality": "Prompt quality",
        "context_selection": "Context selection",
        "safety": "Safety awareness",
        "diff_minimality": "Diff minimality",
    }

    for dim, ds in dimensions.items():
        high_thresh, low_thresh = thresholds.get(dim, (ds.max_score * 0.8, ds.max_score * 0.5))
        label = labels.get(dim, dim)
        if ds.score >= high_thresh:
            strengths.append(f"{label}: {ds.score}/{ds.max_score}")
        elif ds.score <= low_thresh:
            weaknesses.append(f"{label}: {ds.score}/{ds.max_score}")

    return strengths, weaknesses


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_score(
    diff: ParsedDiff,
    events: list[dict[str, Any]],
    validator_results: list[ValidatorResult],
    test_results: list[TestRunResult],
    manifest: Any,
    agent_turns: list[dict[str, Any]],
) -> ScoreReport:
    """Compute the full 7-dimension score report.

    Parameters
    ----------
    diff:
        Parsed representation of the final unified diff.
    events:
        List of ``supervision_events`` rows as plain dicts, ordered by
        ``occurred_at`` ascending.
    validator_results:
        Results from all validators that ran during grading.
    test_results:
        Results from visible + hidden test suites.
    manifest:
        Parsed ``MissionManifest`` instance.
    agent_turns:
        List of ``agent_turns`` rows as plain dicts (for prompt quality).
    """
    d_correctness = _score_final_correctness(diff, validator_results, test_results, manifest)
    d_verification = _score_verification(events, validator_results, manifest)
    d_agent_review = _score_agent_review(events, manifest)
    d_prompt_quality = _score_prompt_quality(agent_turns, manifest)
    d_context = _score_context_selection(events, manifest)
    d_safety = _score_safety(events, validator_results, manifest)
    d_minimality = _score_diff_minimality(diff, manifest)

    dimensions: dict[str, DimensionScore] = {
        "final_correctness": d_correctness,
        "verification": d_verification,
        "agent_review": d_agent_review,
        "prompt_quality": d_prompt_quality,
        "context_selection": d_context,
        "safety": d_safety,
        "diff_minimality": d_minimality,
    }

    total = sum(ds.score for ds in dimensions.values())
    total = max(0, min(total, 100))

    strengths, weaknesses = _narrative(dimensions)
    missed_failure_mode = not _hidden_tests_passed(test_results)

    return ScoreReport(
        total=total,
        dimensions=dimensions,
        strengths=strengths,
        weaknesses=weaknesses,
        missed_failure_mode=missed_failure_mode,
        badges_earned=[],  # populated by badges.compute_badges
    )
