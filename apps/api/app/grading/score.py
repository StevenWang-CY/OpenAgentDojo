"""Grading scoring engine — §11.2 seven-dimension rubric.

All functions are pure (no DB, no I/O, no randomness) and fully deterministic.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from app.grading.diff import ParsedDiff
from app.grading.dimensions import DIMENSION_MAX, RUBRIC_DIMENSIONS
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
            "max": self.max_score,
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


def _coerce_event_timestamp(event: dict[str, Any]) -> datetime | None:
    """Best-effort: turn an event's ``occurred_at`` into a timezone-aware datetime.

    Tries (in order) the top-level ``occurred_at`` key, the payload's
    ``occurred_at`` key, and a final fallback to ``created_at`` /
    ``submitted_at`` on the payload — so a malformed-but-recoverable event
    doesn't poison the rushed-submit signal. Returns ``None`` when nothing
    parseable is available; the caller is expected to log + skip rather than
    silently pretend the timestamp didn't matter.
    """
    raw = event.get("occurred_at")
    payload = event.get("payload") or {}
    if raw is None and isinstance(payload, dict):
        raw = payload.get("occurred_at")
    if raw is None and isinstance(payload, dict):
        raw = payload.get("created_at") or payload.get("submitted_at")
    if raw is None:
        return None
    try:
        if isinstance(raw, str):
            # Py 3.11+ handles trailing "Z" via fromisoformat.
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.fromtimestamp(float(raw))
    except (ValueError, TypeError) as exc:
        logger.warning(
            "[score] could not parse occurred_at {!r} for event {!r}: {}",
            raw,
            event.get("event_type"),
            exc,
        )
        return None


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


def _diff_opened_after_last_patch(events: list[dict[str, Any]]) -> bool:
    """True iff a ``diff.opened`` event followed the LAST ``patch.applied``.

    The grading runner returns events ordered by ``(occurred_at, id)``, so
    iterating forward and remembering the most recent ``patch.applied``
    index suffices. The "after last patch" framing matters because a
    supervisor may have applied several iterations of a patch — we credit
    them for reviewing the operative (final) patch, not just an earlier one.
    """
    last_patch_idx: int | None = None
    for i, e in enumerate(events):
        if e.get("event_type") == "patch.applied":
            last_patch_idx = i
    if last_patch_idx is None:
        return False
    for j in range(last_patch_idx + 1, len(events)):
        if events[j].get("event_type") == "diff.opened":
            return True
    return False


def _hidden_tests_passed(
    test_results: list[TestRunResult], manifest: Any | None = None
) -> bool:
    """Return True iff every hidden suite exited 0.

    Uses the shared :func:`app.grading.runner.is_hidden_suite` predicate so
    the runner-side bucketing and the score-side correctness gate can't drift
    apart. Callers that still pass no manifest get the legacy substring
    fallback (``"hidden" in suite``).
    """
    if manifest is not None:
        # Local import to avoid a circular import between
        # ``app.grading.runner`` and ``app.grading.score``.
        from app.grading.runner import is_hidden_suite

        hidden = [r for r in test_results if is_hidden_suite(manifest, r.suite)]
    else:
        hidden = [r for r in test_results if "hidden" in r.suite.lower()]
    if not hidden:
        return False  # No hidden tests = treat as failed for scoring
    return all(r.exit_code == 0 for r in hidden)


def _manifest_has_visible_suites(manifest: Any | None) -> bool:
    """Return True iff the mission manifest configured any visible suites.

    Visible suites live under ``manifest.repo.test_commands`` — a dict keyed
    by suite name. An empty/absent dict means the mission ships no visible
    runner at all, so the correctness rubric should treat the visible-tests
    gate as N/A rather than punishing the supervisor for a missing suite the
    author never wired up.
    """
    if manifest is None:
        return True
    repo = getattr(manifest, "repo", None)
    cmds = getattr(repo, "test_commands", None) if repo is not None else None
    if not isinstance(cmds, dict):
        return False
    return len(cmds) > 0


def _visible_tests_passed(
    test_results: list[TestRunResult], manifest: Any | None = None
) -> bool:
    """Return True iff every visible *unit/integration* suite exited 0.

    Lint and typecheck are excluded from this gate — they have their own
    verification credit (§11.2.2) and a flaky lint config should not
    zero-out the correctness dimension when the tests themselves are green.

    Distinguishes the two failure modes that used to collapse into
    ``return False`` (P0-B4 / rubric-1 follow-up):

    * *No visible suites configured by the manifest* — the mission never
      declared ``repo.test_commands``, so there is nothing to gate on.
      Treated as N/A (return True). The loader already refuses missions that
      declare ``visible_tests`` without a matching ``test_commands``, so this
      branch only fires for missions whose author intentionally ships only a
      hidden suite.
    * *Visible suites configured but none passed (or none were even run)* —
      return False; the supervisor really did miss the gate.
    """
    visible = [
        r
        for r in test_results
        if "hidden" not in r.suite.lower() and r.suite.lower() not in {"lint", "typecheck"}
    ]
    if not _manifest_has_visible_suites(manifest):
        # No visible suites configured by the mission manifest → N/A.
        return True
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

    hidden_pass = _hidden_tests_passed(test_results, manifest)
    visible_pass = _visible_tests_passed(test_results, manifest)
    visible_na = not _manifest_has_visible_suites(manifest)

    # Regression proxy: existing tests (visible) still pass after patch.
    # When the mission ships no visible suites at all, there is no regression
    # signal to evaluate — so the regression credit is awarded by default
    # (otherwise a hidden-only mission could only score 24/30 even on a
    # perfect submission). The hidden-test pass remains the dominant gate.
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

    if visible_na:
        # N/A: mission ships no visible suites — surface the marker but
        # still award the credit so the supervisor isn't penalised for the
        # author's design choice.
        score += 8
        signals.append("+8: visible tests N/A (mission ships no visible suites)")
    elif visible_pass:
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
# Dimension 2: Verification Discipline (max 15)
# ---------------------------------------------------------------------------


def _score_verification(
    events: list[dict[str, Any]],
    validator_results: list[ValidatorResult],
    manifest: Any,
) -> DimensionScore:
    score = 0
    signals: list[str] = []
    max_score = DIMENSION_MAX["verification"]

    command_run_events = _events_of_type(events, "command.run")
    has_any_command = len(command_run_events) > 0

    # +6: test command run that matches require_targeted_test.
    targeted_test_pattern: str | None = None
    reward_signals = getattr(manifest, "reward_signals", None)
    verification_cfg = getattr(reward_signals, "verification", None)
    if reward_signals is not None and verification_cfg is None:
        logger.debug(
            "[score] manifest reward_signals missing 'verification' section "
            "(id={!r}) — falling through with no targeted-test pattern",
            getattr(manifest, "id", "<unknown>"),
        )
    if verification_cfg is not None:
        targeted_test_pattern = getattr(verification_cfg, "require_targeted_test", None)

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
        score += 6
        signals.append("+6: targeted test command run")
    else:
        signals.append("+0: no targeted test command found")

    # +3: typecheck command run.
    has_typecheck = any(
        e.get("payload", {}).get("category") == "typecheck" for e in command_run_events
    )
    if has_typecheck:
        score += 3
        signals.append("+3: typecheck run")
    else:
        signals.append("+0: no typecheck run")

    # +2: lint command run.
    has_lint = any(e.get("payload", {}).get("category") == "lint" for e in command_run_events)
    if has_lint:
        score += 2
        signals.append("+2: lint run")
    else:
        signals.append("+0: no lint run")

    # +4: regression_test_required validator passed.
    regression_val = _find_validator(validator_results, "regression_test_required")
    if regression_val is not None and regression_val.passed:
        score += 4
        signals.append("+4: regression test validator passed")
    else:
        signals.append("+0: regression test validator not passed")

    # -6: zero command.run events of any kind.
    if not has_any_command:
        score -= 6
        signals.append("-6: no command.run events at all")

    return DimensionScore(score=max(0, min(score, max_score)), max_score=max_score, signals=signals)


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
        agent_dt = _coerce_event_timestamp(last_agent)
        submit_dt = _coerce_event_timestamp(first_submit)
        if agent_dt is None or submit_dt is None:
            # Don't silently treat the missing timestamp as "not rushed" — log
            # so a schema regression is loud, then skip the heuristic.
            logger.warning(
                "[score] rushed-submit heuristic skipped: agent_dt={} submit_dt={}",
                agent_dt,
                submit_dt,
            )
        else:
            delta = (submit_dt - agent_dt).total_seconds()
            if delta <= 15 and not diff_opened_events:
                rushed_submit = True

    if rushed_submit:
        signals.append("0/15: submit within 15s of agent.responded with no diff opened (hard zero)")
        return DimensionScore(score=0, max_score=15, signals=signals)

    # +6: diff.opened event occurred AFTER the LAST patch.applied event.
    #
    # The previous heuristic (``_event_occurred_after("patch.applied",
    # "diff.opened")``) rewarded *any* diff opened after the *first* patch —
    # which silently double-counted a stale review of an early patch even
    # when the supervisor never looked at the most recent operative patch.
    # The score is for opening the diff AFTER the patch the supervisor is
    # actually about to submit, so we walk events sorted by
    # (occurred_at, id) (the runner already returns them in this order),
    # find the LAST ``patch.applied``, and check whether any
    # ``diff.opened`` followed it.
    diff_after_patch = _diff_opened_after_last_patch(events)
    if diff_after_patch:
        score += 6
        signals.append("+6: diff opened after the most recent patch applied")
    else:
        signals.append("+0: diff not opened after the most recent patch applied")

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
        prompt_text = payload.get("text", payload.get("prompt", "")).lower()

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
    """Score prompt quality as the AVERAGE of the LAST 3 turns.

    Why average (not max-of-all)? The previous heuristic picked the single
    best prompt across all turns, which rewarded a supervisor who lobbed in
    one strong prompt then degraded into ``fix it`` follow-ups. Averaging the
    final few turns measures consistency — the score reflects what the
    supervisor was actually doing as they closed in on submission.

    Why "last 3" specifically? It's a small enough window that a one-off
    weak revise prompt still shows up in the rolling average, but large
    enough to forgive a brief clarification turn (e.g. ``yes, do that``)
    sandwiched between two substantive prompts. Fewer than 3 turns → use
    however many exist. Zero prompts → score 0 (we don't crash).
    """
    signals: list[str] = []
    max_score = DIMENSION_MAX["prompt_quality"]

    must_include: list[str] = []
    bonus_keywords: list[str] = []
    reward_signals = getattr(manifest, "reward_signals", None)
    prompt_cfg = getattr(reward_signals, "prompt_quality", None)
    if reward_signals is not None and prompt_cfg is None:
        logger.debug(
            "[score] manifest reward_signals missing 'prompt_quality' section "
            "(id={!r}) — using empty keyword lists",
            getattr(manifest, "id", "<unknown>"),
        )
    if prompt_cfg is not None:
        must_include = list(getattr(prompt_cfg, "must_include_any", []) or [])
        bonus_keywords = list(getattr(prompt_cfg, "bonus_keywords", []) or [])

    def _score_one_turn(prompt: str) -> tuple[int, list[str]]:
        prompt_lower = prompt.lower()
        turn_score = 0
        turn_signals: list[str] = []

        if len(prompt) >= 80:
            turn_score += 2
            turn_signals.append("+2: prompt >= 80 chars")

        if any(kw.lower() in prompt_lower for kw in must_include):
            turn_score += 2
            turn_signals.append("+2: required keyword present")

        bonus_hit = sum(1 for kw in bonus_keywords if kw.lower() in prompt_lower)
        bonus_pts = min(bonus_hit, 3)
        if bonus_pts:
            turn_score += bonus_pts
            turn_signals.append(f"+{bonus_pts}: {bonus_pts} bonus keyword(s)")

        if "test" in prompt_lower or "regression" in prompt_lower:
            turn_score += 2
            turn_signals.append("+2: test/regression mentioned")

        scope_phrases = ["do not modify", "minimal", "without changing", "only change"]
        if any(p in prompt_lower for p in scope_phrases):
            turn_score += 2
            turn_signals.append("+2: scope phrase present")

        if len(prompt) < 40:
            turn_score -= 3
            turn_signals.append("-3: prompt < 40 chars")

        vague_phrases = ["fix it", "make it work", "just fix", "do it"]
        prompt_stripped = prompt.strip().lower()
        if any(prompt_stripped == vague for vague in vague_phrases) or (
            all(vague in prompt_stripped for vague in ["fix"]) and len(prompt) < 20
        ):
            turn_score -= 2
            turn_signals.append("-2: vague-only prompt")

        return max(0, min(turn_score, max_score)), turn_signals

    # Filter to turns that actually carry a prompt — keep ordering, then take
    # the trailing 3.
    prompts: list[str] = []
    for turn in agent_turns:
        if not isinstance(turn, dict):
            continue
        prompt = turn.get("user_prompt", turn.get("prompt", "")) or ""
        if prompt:
            prompts.append(prompt)

    if not prompts:
        return DimensionScore(
            score=0,
            max_score=max_score,
            signals=["no agent turns with user_prompt — scored 0/10"],
        )

    tail = prompts[-3:]
    per_turn = [_score_one_turn(p) for p in tail]
    avg = round(sum(s for s, _ in per_turn) / len(per_turn))

    signals.append(f"averaged last {len(tail)} of {len(prompts)} prompt(s) → {avg}/{max_score}")
    for idx, (s, sigs) in enumerate(per_turn, start=1):
        signals.append(f"turn -{len(tail) - idx + 1}: {s}/{max_score} [{'; '.join(sigs) or '-'}]")

    return DimensionScore(score=max(0, min(avg, max_score)), max_score=max_score, signals=signals)


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
    expected_context = getattr(manifest, "expected_context", None)
    if expected_context is None:
        logger.debug(
            "[score] manifest missing 'expected_context' section "
            "(id={!r}) — context-selection will score 0",
            getattr(manifest, "id", "<unknown>"),
        )
    else:
        required = list(getattr(expected_context, "required", []) or [])
        recommended = list(getattr(expected_context, "recommended", []) or [])
        discouraged = list(getattr(expected_context, "discouraged", []) or [])

    # Selected files = the LAST ``context.selected`` event that preceded the
    # LAST ``prompt.submitted`` event. That's the context the supervisor
    # actually shipped TO the agent — selections made *after* submitting the
    # prompt are post-hoc exploration and should not count toward (or
    # against) the prompt-time score.
    #
    # If no prompt has been submitted yet (e.g. mission graded before the
    # user submitted), fall back to the MAX recall across all selections so
    # an early correct selection still earns credit.
    selection_payloads: list[dict[str, Any]] = []
    for e in events:
        if e.get("event_type") == "context.selected":
            selection_payloads.append(e.get("payload") or {})

    last_prompt_idx: int | None = None
    for i, e in enumerate(events):
        if e.get("event_type") == "prompt.submitted":
            last_prompt_idx = i

    operative_files: list[str] | None = None
    if last_prompt_idx is not None:
        for j in range(last_prompt_idx - 1, -1, -1):
            if events[j].get("event_type") == "context.selected":
                payload = events[j].get("payload") or {}
                operative_files = list(payload.get("files", []))
                break

    required_set = set(required)
    recommended_set = set(recommended)
    discouraged_set = set(discouraged)
    max_score = DIMENSION_MAX["context_selection"]

    def _score_selection(files: list[str]) -> tuple[int, float, float, int]:
        selected_set = set(files)
        required_hit = (
            len(selected_set & required_set) / len(required_set) if required_set else 0.0
        )
        recommended_hit = len(selected_set & recommended_set) / max(1, len(recommended_set))
        discouraged_hit = len(selected_set & discouraged_set)
        raw = round(required_hit * 7 + recommended_hit * 3) - min(3, discouraged_hit)
        return max(0, min(raw, max_score)), required_hit, recommended_hit, discouraged_hit

    if last_prompt_idx is not None:
        # A prompt was submitted — the operative selection is whatever was
        # in flight at that instant (which may be empty if the supervisor
        # never picked any files before pressing submit).
        score, required_hit, recommended_hit, discouraged_hit = _score_selection(
            operative_files or []
        )
        signals.append(
            f"required_hit={required_hit:.2f} recommended_hit={recommended_hit:.2f} "
            f"discouraged_hit={discouraged_hit}"
        )
        if operative_files is not None:
            signals.append(f"selected {len(operative_files)} file(s) at prompt time")
        else:
            signals.append("prompt submitted with no preceding context.selected event")
    elif selection_payloads:
        # No prompt submitted yet — fall back to the MAX score across all
        # selections so the supervisor isn't penalised for still being in
        # the exploration phase. Documented in the dimension docstring.
        results = [
            _score_selection(list(p.get("files", []) or [])) for p in selection_payloads
        ]
        best = max(results, key=lambda r: r[0])
        score, required_hit, recommended_hit, discouraged_hit = best
        signals.append(
            f"required_hit={required_hit:.2f} recommended_hit={recommended_hit:.2f} "
            f"discouraged_hit={discouraged_hit}"
        )
        signals.append(
            f"no prompt.submitted yet — used max across "
            f"{len(selection_payloads)} context.selected event(s)"
        )
    else:
        score, required_hit, recommended_hit, discouraged_hit = _score_selection([])
        signals.append(
            f"required_hit={required_hit:.2f} recommended_hit={recommended_hit:.2f} "
            f"discouraged_hit={discouraged_hit}"
        )
        signals.append("no context.selected event found")

    return DimensionScore(score=score, max_score=max_score, signals=signals)


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
    # after a validator.flag event for that file). Pre-index by event identity
    # so we avoid the O(n²) ``events.index(...)`` lookup inside the loop
    # (P1-B20).
    validator_flag_events = _events_of_type(events, "validator.flag")
    reverted_after_flag = False
    if validator_flag_events:
        event_index: dict[int, int] = {id(e): i for i, e in enumerate(events)}
        for flag_evt in validator_flag_events:
            flag_idx = event_index.get(id(flag_evt), -1)
            if flag_idx < 0:
                continue
            for i in range(flag_idx + 1, len(events)):
                if events[i].get("event_type") in ("file.reverted", "file.edited"):
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
    reward_signals = getattr(manifest, "reward_signals", None)
    safety_cfg = getattr(reward_signals, "safety", None)
    if reward_signals is not None and safety_cfg is None:
        logger.debug(
            "[score] manifest reward_signals missing 'safety' section "
            "(id={!r}) — safety check will treat all commands as allowed",
            getattr(manifest, "id", "<unknown>"),
        )
    if safety_cfg is not None:
        banned_commands = list(getattr(safety_cfg, "must_not_run_commands", []) or [])

    command_run_events = _events_of_type(events, "command.run")
    has_banned = False
    for e in command_run_events:
        cmd = e.get("payload", {}).get("command", "") or ""
        if _command_matches_banned(cmd, banned_commands):
            has_banned = True
            break

    if not has_banned:
        score += 1
        signals.append("+1: no banned commands run")
    else:
        signals.append("+0: banned command was run")

    return DimensionScore(score=min(score, 10), max_score=10, signals=signals)


def _command_matches_banned(cmd: str, banned: list[str]) -> bool:
    """True if ``cmd`` runs anything matching one of the ``banned`` patterns.

    Tries argv-aware matching first via :func:`shlex.split` so a wrapper
    like ``sh -c 'rm -rf /'`` is still caught when the banned list contains
    ``rm -rf`` or ``rm``. Each banned pattern is itself shlex-split into
    tokens, then we check whether those tokens appear as a contiguous
    subsequence anywhere in the parsed argv (including inside any single
    ``-c <script>`` argument we recurse into).

    Falls back to the legacy literal-substring check if either side fails
    to parse — malformed shell shouldn't silently bypass the gate.
    """
    if not cmd or not banned:
        return False

    def _argv_for(text: str) -> list[str] | None:
        try:
            return shlex.split(text)
        except ValueError:
            return None

    cmd_argv = _argv_for(cmd)
    if cmd_argv is None:
        return any(b in cmd for b in banned)

    # Recursively flatten nested shells: ``sh -c "rm -rf /"`` ->
    # ["sh", "-c", "rm -rf /"]. Split the inner script as a separate argv
    # so the matcher can see the inner tokens too.
    flat_argvs: list[list[str]] = [cmd_argv]
    for i, tok in enumerate(cmd_argv):
        if tok in {"-c", "--command"} and i + 1 < len(cmd_argv):
            nested = _argv_for(cmd_argv[i + 1])
            if nested:
                flat_argvs.append(nested)

    for banned_pat in banned:
        if not banned_pat:
            continue
        banned_argv = _argv_for(banned_pat)
        if not banned_argv:
            # Pattern didn't parse — fall back to literal substring.
            if banned_pat in cmd:
                return True
            continue
        for argv in flat_argvs:
            if _argv_contains(argv, banned_argv):
                return True
        # Last-resort literal substring (covers patterns like ``rm -rf /``
        # where the user might or might not have an exact space layout).
        if banned_pat in cmd:
            return True
    return False


def _argv_contains(argv: list[str], needle: list[str]) -> bool:
    """True if ``needle`` appears as a contiguous subsequence in ``argv``."""
    if not needle or len(needle) > len(argv):
        return False
    for i in range(len(argv) - len(needle) + 1):
        if argv[i : i + len(needle)] == needle:
            return True
    return False


# ---------------------------------------------------------------------------
# Dimension 7: Diff Minimality (max 10)
# ---------------------------------------------------------------------------


def _score_diff_minimality(diff: ParsedDiff, manifest: Any) -> DimensionScore:
    """Score how compact the final diff is, gauged against the mission p50.

    Uses ``max(added, removed)`` instead of just ``added_lines_total`` so that
    destructive minimisation — wiping out 200 lines while only adding 5 — is
    not silently rewarded with a perfect minimality score. Either direction
    of churn pushes the diff away from minimal; the rubric should treat
    them symmetrically.
    """
    signals: list[str] = []
    max_score = DIMENSION_MAX["diff_minimality"]

    p50 = getattr(manifest, "expected_diff_lines_p50", 20)
    added = diff.added_lines_total()
    removed = diff.removed_lines_total()
    churn = max(added, removed)
    ratio = churn / max(1, p50)

    if ratio <= 1.0:
        score = 10
        signals.append(f"ratio={ratio:.2f} <= 1.0 → 10/10")
    elif ratio <= 1.5:
        score = 8
        signals.append(f"ratio={ratio:.2f} in (1.0, 1.5] → 8/10")
    elif ratio <= 2.0:
        score = 6
        signals.append(f"ratio={ratio:.2f} in (1.5, 2.0] → 6/10")
    elif ratio <= 3.0:
        score = 4
        signals.append(f"ratio={ratio:.2f} in (2.0, 3.0] → 4/10")
    else:
        score = 0
        signals.append(f"ratio={ratio:.2f} > 3.0 → 0/10")

    signals.append(
        f"churn=max(added={added}, removed={removed})={churn} p50={p50}"
    )
    return DimensionScore(score=score, max_score=max_score, signals=signals)


# ---------------------------------------------------------------------------
# Strengths / Weaknesses narrative
# ---------------------------------------------------------------------------


def _narrative(dimensions: dict[str, DimensionScore]) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []

    # Strength = >= 80% of max, weakness = <= 40% of max. Computed from the
    # central ``DIMENSION_MAX`` so adjusting a dimension's weight in
    # ``app.grading.dimensions`` automatically reflows the narrative
    # thresholds (the old hard-coded table drifted whenever a max changed).
    thresholds = {
        name: (round(max_s * 0.8), round(max_s * 0.4))
        for name, max_s in DIMENSION_MAX.items()
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
    # Compute each dimension into a name -> DimensionScore lookup, then
    # assemble the final dict by iterating the canonical
    # :data:`RUBRIC_DIMENSIONS` order. Keying off the central tuple means
    # adding/renaming/removing a dimension only needs to be done in
    # ``app.grading.dimensions`` — the score report layout follows
    # automatically.
    by_name: dict[str, DimensionScore] = {
        "final_correctness": _score_final_correctness(
            diff, validator_results, test_results, manifest
        ),
        "verification": _score_verification(events, validator_results, manifest),
        "agent_review": _score_agent_review(events, manifest),
        "prompt_quality": _score_prompt_quality(agent_turns, manifest),
        "context_selection": _score_context_selection(events, manifest),
        "safety": _score_safety(events, validator_results, manifest),
        "diff_minimality": _score_diff_minimality(diff, manifest),
    }
    dimensions: dict[str, DimensionScore] = {name: by_name[name] for name, _ in RUBRIC_DIMENSIONS}

    total = sum(ds.score for ds in dimensions.values())
    total = max(0, min(total, 100))

    strengths, weaknesses = _narrative(dimensions)
    missed_failure_mode = not _hidden_tests_passed(test_results, manifest)

    return ScoreReport(
        total=total,
        dimensions=dimensions,
        strengths=strengths,
        weaknesses=weaknesses,
        missed_failure_mode=missed_failure_mode,
        badges_earned=[],  # populated by badges.compute_badges
    )
