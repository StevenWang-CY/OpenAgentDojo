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
    # P0-2 — supervision event ids that contributed to this dimension's
    # signals (e.g. the command.run ids for verification, the diff.opened
    # ids for agent_review). Stamped after scoring by
    # :func:`_attach_evidence`. Empty list when no events were the
    # operative input (signal came from a validator) — the FE renders
    # those without the "→ events #N" affordance.
    evidence_event_ids: list[int] = field(default_factory=list)

    @property
    def pending(self) -> bool:
        """Sentinel: -1 means measurement-unavailable (e.g. prompt_quality
        when the LLM judge cache is cold and the model is unreachable)."""
        return self.score < 0

    def to_dict(self) -> dict[str, Any]:
        # The wire format exposes pending dimensions as ``null`` rather than
        # the internal -1 sentinel so the frontend never has to know about
        # the magic number.
        return {
            "score": None if self.pending else self.score,
            "max": self.max_score,
            "signals": self.signals,
            "evidence_event_ids": list(self.evidence_event_ids),
        }


@dataclass
class StrengthEntry:
    """Evidence-bearing strength entry (P0-2).

    The wire format also accepts the legacy ``str`` shape so older
    submissions don't break on read — the FE union-narrows on type.
    """

    message: str
    dimension: str
    evidence_event_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "dimension": self.dimension,
            "evidence_event_ids": list(self.evidence_event_ids),
        }


@dataclass
class WeaknessEntry:
    """Evidence-bearing weakness entry. Identical shape to StrengthEntry —
    we ship them as separate types so the FE can colour-code each."""

    message: str
    dimension: str
    evidence_event_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "dimension": self.dimension,
            "evidence_event_ids": list(self.evidence_event_ids),
        }


@dataclass
class ScoreReport:
    total: int
    dimensions: dict[str, DimensionScore]
    strengths: list[StrengthEntry]
    weaknesses: list[WeaknessEntry]
    missed_failure_mode: bool
    badges_earned: list[str]
    # Per-dimension diagnostic narrative (P2-1). Each entry tells the user
    # WHY they scored low on a dimension and WHICH MISSIONS to try next.
    # Empty when there are no weaknesses (the user nailed everything).
    feedback_narrative: list[dict[str, Any]] = field(default_factory=list)
    # The effective maximum total this report could have reached, given
    # any pending dimensions (P0-1). 100 in the normal case; drops to 90
    # when prompt_quality is pending; etc. The FE should render the
    # score as ``total / effective_max`` rather than hardcoding /100.
    effective_max: int = 100
    # P0-4 — when set, a post-grading rule capped ``total``. Currently the
    # only legal value is ``"gave_up"`` (capped at 50/100). Mirrored to
    # ``submissions.score_cap_reason`` by the grading runner; the FE
    # renders a chip in the report header explaining the cap.
    # NULL means no cap was applied — the dimension sum is the honest
    # total. Cap is applied AFTER ``effective_max`` so the breakdown
    # remains honest and the cap surfaces as a single, named field.
    score_cap_reason: str | None = None
    # P0-4 — the uncapped total, persisted so the FE can render "would
    # have scored 82" beside the capped 50. ``None`` when no cap was
    # applied (saving a few bytes on the common path).
    uncapped_total: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "strengths": [s.to_dict() for s in self.strengths],
            "weaknesses": [w.to_dict() for w in self.weaknesses],
            "missed_failure_mode": self.missed_failure_mode,
            "badges_earned": self.badges_earned,
            "feedback_narrative": list(self.feedback_narrative),
            "effective_max": self.effective_max,
            "score_cap_reason": self.score_cap_reason,
            "uncapped_total": self.uncapped_total,
        }


# P0-4 — score cap policy. The give-up affordance applies this cap at the
# report-total level, leaving dimension scores untouched so the breakdown
# remains an honest measurement of the supervisor's work. ADR 0010
# documents the choice of 50.
GAVE_UP_SCORE_CAP: int = 50


def apply_score_cap(
    report: ScoreReport,
    *,
    reason: str,
    cap: int,
) -> ScoreReport:
    """Mutate ``report`` in-place to apply a hard cap on ``total``.

    Used by the give-up flow (P0-4) — the dimension scores remain honest;
    only ``total``, ``score_cap_reason`` and ``uncapped_total`` change.
    The cap is only applied when ``total`` actually exceeds ``cap`` so a
    user who gave up at minute 11 with a 38/100 attempt still sees their
    real score (and ``score_cap_reason`` reflects the deliberate give-up
    so the FE can render the chip honestly even when the cap was not
    binding).
    """
    report.score_cap_reason = reason
    report.uncapped_total = report.total
    if report.total > cap:
        report.total = cap
    return report


# ---------------------------------------------------------------------------
# P0-2 — evidence-event collection per dimension.
# ---------------------------------------------------------------------------


# Map dimension → ordered set of event types that "evidenced" the score.
# The collection is post-hoc (after compute_score returns each
# DimensionScore) so the dimension scorers don't need to thread event ids
# through every helper. The mapping favours legibility over exactness:
# the FE renders evidence as "show me the related events", not "these are
# the literal bytes that drove the score".
_EVIDENCE_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "final_correctness": ("patch.applied",),
    "verification": ("command.run",),
    "agent_review": ("diff.opened",),
    "prompt_quality": ("prompt.submitted",),
    "context_selection": ("context.selected",),
    "safety": ("validator.flag", "command.run"),
    "diff_minimality": ("patch.applied",),
}

# For ``verification`` we only want command.run events whose category is in
# the verification set. Other dimensions accept every matching event.
_VERIFICATION_COMMAND_CATEGORIES: frozenset[str] = frozenset(
    {"test", "typecheck", "lint"}
)


def _attach_evidence(
    dimensions: dict[str, DimensionScore], events: list[dict[str, Any]]
) -> None:
    """Stamp ``evidence_event_ids`` onto every dimension in-place.

    Output is **sorted ascending and de-duplicated** so:
      * Two grading replays of the same event stream emit byte-identical
        ``score_report`` JSONB (load-bearing for the determinism invariant
        documented in ADR 0006).
      * The FE evidence-chip renderer doesn't draw the same #N twice when
        a session happens to emit two events with the same id (which can
        legitimately happen across distinct event types).
    """
    for dim_name, types in _EVIDENCE_EVENT_TYPES.items():
        ds = dimensions.get(dim_name)
        if ds is None:
            continue
        seen: set[int] = set()
        for ev in events:
            if ev.get("event_type") not in types:
                continue
            if dim_name == "verification":
                payload = ev.get("payload") or {}
                if payload.get("category") not in _VERIFICATION_COMMAND_CATEGORIES:
                    continue
            eid = ev.get("id")
            if isinstance(eid, int):
                seen.add(eid)
        ds.evidence_event_ids = sorted(seen)


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


def _diff_dwell_milliseconds(events: list[dict[str, Any]]) -> int:
    """Return the supervisor's dwell time on the last ``diff.opened``, in ms.

    Dwell = wall-clock delta from the *final* ``diff.opened`` event (after
    the most recent ``patch.applied``) to the next event of any kind in the
    session. If no event follows, fall back to the time between the diff
    open and the ``submission.requested`` event. If neither is parseable,
    return 0 (caller treats < 5000 as open-and-close).

    Why "last diff.opened after last patch.applied"? That's the diff the
    supervisor was actually looking at when they made the decision to
    submit. Earlier opens of stale diffs don't reflect the operative
    review.
    """
    last_patch_idx = -1
    for i, e in enumerate(events):
        if e.get("event_type") == "patch.applied":
            last_patch_idx = i
    last_diff_idx = -1
    for i in range(last_patch_idx + 1, len(events)):
        if events[i].get("event_type") == "diff.opened":
            last_diff_idx = i
    if last_diff_idx < 0:
        return 0
    diff_dt = _coerce_event_timestamp(events[last_diff_idx])
    if diff_dt is None:
        return 0
    for j in range(last_diff_idx + 1, len(events)):
        next_dt = _coerce_event_timestamp(events[j])
        if next_dt is None:
            continue
        delta_s = (next_dt - diff_dt).total_seconds()
        if delta_s < 0:
            continue
        return int(delta_s * 1000)
    return 0


def _hidden_test_suites(
    test_results: list[TestRunResult], manifest: Any | None = None
) -> list[TestRunResult]:
    """Filter to the hidden suites, using the shared bucketing predicate."""
    if manifest is not None:
        # Local import to avoid a circular import between
        # ``app.grading.runner`` and ``app.grading.score``.
        from app.grading.runner import is_hidden_suite

        return [r for r in test_results if is_hidden_suite(manifest, r.suite)]
    return [r for r in test_results if "hidden" in r.suite.lower()]


def _hidden_tests_passed(
    test_results: list[TestRunResult], manifest: Any | None = None
) -> bool:
    """Return True iff every hidden suite exited 0.

    Uses the shared :func:`app.grading.runner.is_hidden_suite` predicate so
    the runner-side bucketing and the score-side correctness gate can't drift
    apart. Callers that still pass no manifest get the legacy substring
    fallback (``"hidden" in suite``).
    """
    hidden = _hidden_test_suites(test_results, manifest)
    if not hidden:
        return False  # No hidden tests = treat as failed for scoring
    return all(r.exit_code == 0 for r in hidden)


def _hidden_test_counts(
    test_results: list[TestRunResult], manifest: Any | None = None
) -> tuple[int, int, list[tuple[str, int, int]]]:
    """Aggregate per-case hidden-test pass counts.

    Returns ``(total_passed, total_cases, per_suite)`` where ``per_suite`` is
    a list of ``(suite_name, passed, total)`` tuples. ``total_cases`` excludes
    skipped tests (skipped assertions measure nothing).

    Defensive: if a suite reports zero passed+failed (test runner crashed,
    collection errored) but exit_code != 0, that suite contributes one
    synthetic failed case so the proportional credit reflects "this suite did
    not work". If exit_code == 0 with zero counts, that suite contributes one
    synthetic passing case (the runner declared success but emitted no count
    metadata — rare for our pinned reporters but worth tolerating).
    """
    hidden = _hidden_test_suites(test_results, manifest)
    total_passed = 0
    total_cases = 0
    per_suite: list[tuple[str, int, int]] = []
    for r in hidden:
        passed = r.passed
        failed = r.failed
        n = passed + failed
        if n == 0:
            if r.exit_code == 0:
                passed, n = 1, 1
            else:
                passed, n = 0, 1
        total_passed += passed
        total_cases += n
        per_suite.append((r.suite, passed, n))
    return total_passed, total_cases, per_suite


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
    # Use the shared is_hidden_suite predicate so manifest-declared hidden
    # suite names (e.g. "e2e-canary") that don't contain the substring
    # "hidden" are still correctly bucketed away from the visible gate.
    from app.grading.runner import is_hidden_suite

    visible = [
        r
        for r in test_results
        if not is_hidden_suite(manifest, r.suite)
        and r.suite.lower() not in {"lint", "typecheck"}
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

    # Proportional hidden-test credit (P0-3): a supervisor who fixes 9/10
    # root-cause behaviours has materially different supervision quality from
    # one who fixes 0/10. The previous rule (binary: all-pass → 12, any-fail
    # → 0) collapsed both into the same score and discarded the granularity
    # the hidden suites already carry.
    hidden_passed_n, hidden_total_n, hidden_per_suite = _hidden_test_counts(
        test_results, manifest
    )
    if hidden_total_n == 0:
        # No hidden suites configured — treat as zero credit + zero ceiling
        # adjustment. Matches the legacy "no hidden tests = failed" rule.
        hidden_credit = 0
        signals.append("+0: no hidden tests configured for this mission")
    else:
        ratio = hidden_passed_n / hidden_total_n
        hidden_credit = round(12 * ratio)
        if hidden_passed_n == hidden_total_n:
            signals.append(
                f"+{hidden_credit}: all hidden tests pass "
                f"({hidden_passed_n}/{hidden_total_n})"
            )
        else:
            signals.append(
                f"+{hidden_credit}: hidden tests "
                f"{hidden_passed_n}/{hidden_total_n} passed "
                f"(proportional credit of 12)"
            )
        for suite_name, suite_passed, suite_total in hidden_per_suite:
            signals.append(
                f"  hidden suite {suite_name}: {suite_passed}/{suite_total} passed"
            )
    score += hidden_credit

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

    # Ceiling rule: when any hidden test failed, cap the dimension at
    # (18 non-hidden points + proportional hidden_credit). Equivalent to the
    # old "cap at 18" when hidden_credit == 0, but lets a 9/10-passing
    # submission climb to 29/30 instead of being floored.
    if not hidden_pass:
        ceiling = 18 + hidden_credit
        if score > ceiling:
            signals.append(
                f"capped at {ceiling} (hidden tests partially failed, "
                f"was {score}; 18 non-hidden + {hidden_credit} hidden credit)"
            )
            score = ceiling

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

    # Targeted-test credit (P1-1): running the test is necessary but not
    # sufficient. To earn the full +6, the supervisor must either run a
    # passing test (engagement implicit — they got green and moved on) OR
    # demonstrate engagement after a failing test by editing a file or
    # submitting a follow-up prompt. Running a failing test and ignoring
    # the result earns partial credit only (+3): the supervisor ran the
    # command, but did not act on what it told them.
    targeted_test_events: list[tuple[int, dict[str, Any]]] = []
    for i, e in enumerate(events):
        if e.get("event_type") != "command.run":
            continue
        payload = e.get("payload", {}) or {}
        if payload.get("category") != "test":
            continue
        command = payload.get("command", "") or ""
        if targeted_test_pattern is None or re.search(
            targeted_test_pattern, command, re.IGNORECASE
        ):
            targeted_test_events.append((i, payload))

    if not targeted_test_events:
        signals.append("+0: no targeted test command found")
    else:
        # Bucket by outcome. Any passing run → engagement is implicit.
        # Otherwise check for follow-up action after the most recent failure.
        any_pass = any(
            int(p.get("exit_code", 0) or 0) == 0 for _, p in targeted_test_events
        )
        if any_pass:
            score += 6
            signals.append("+6: targeted test command run (and passed)")
        else:
            last_fail_idx = targeted_test_events[-1][0]
            follow_up = False
            follow_up_kind: str | None = None
            for j in range(last_fail_idx + 1, len(events)):
                et = events[j].get("event_type")
                if et in {"file.edited", "file.reverted"}:
                    follow_up = True
                    follow_up_kind = et
                    break
                if et == "prompt.submitted":
                    follow_up = True
                    follow_up_kind = et
                    break
            if follow_up:
                score += 6
                signals.append(
                    f"+6: targeted test ran (failed) and supervisor "
                    f"followed up with {follow_up_kind}"
                )
            else:
                score += 3
                signals.append(
                    "+3: targeted test ran but exited non-zero with no "
                    "follow-up edit or prompt — partial credit only"
                )

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

    # +6: diff.opened event occurred AFTER the LAST patch.applied event AND
    # the supervisor actually dwelt on it for >= 5s (P1-2).
    #
    # The previous heuristic (``_event_occurred_after("patch.applied",
    # "diff.opened")``) rewarded *any* diff opened after the *first* patch —
    # which silently double-counted a stale review of an early patch even
    # when the supervisor never looked at the most recent operative patch.
    # The score is for opening the diff AFTER the patch the supervisor is
    # actually about to submit, so we walk events sorted by
    # (occurred_at, id) (the runner already returns them in this order),
    # find the LAST ``patch.applied``, and check whether any
    # ``diff.opened`` followed it. We also require that the supervisor
    # dwelt on the diff for at least 5 seconds before the next action —
    # a sub-second open-and-close is event-puppeteering, not review.
    diff_after_patch = _diff_opened_after_last_patch(events)
    dwell_ms = _diff_dwell_milliseconds(events)
    if diff_after_patch and dwell_ms >= 5000:
        score += 6
        signals.append(
            f"+6: diff opened after the most recent patch applied "
            f"(dwell {dwell_ms} ms)"
        )
    elif diff_after_patch:
        # Open-and-close inside 5s — give partial credit (+3) for at
        # least opening the panel; full credit requires real review time.
        score += 3
        signals.append(
            f"+3: diff opened but dwell time {dwell_ms} ms < 5000 ms "
            "(open-and-close, partial credit only)"
        )
    else:
        signals.append("+0: diff not opened after the most recent patch applied")

    # +5: any *meaningful* file.edited or file.reverted event (P1-2).
    #
    # A file.edited event with added=0 and removed=0 is a no-op write
    # (Monaco serialises every save even if the buffer is unchanged), and
    # crediting it is pure event-puppeteering. file.reverted always
    # represents an action so we accept it unconditionally; file.edited
    # must change at least one line.
    meaningful_edit = False
    for ev in events:
        et = ev.get("event_type")
        if et == "file.reverted":
            meaningful_edit = True
            break
        if et == "file.edited":
            payload = ev.get("payload", {}) or {}
            added = int(payload.get("added", 0) or 0)
            removed = int(payload.get("removed", 0) or 0)
            if added > 0 or removed > 0:
                meaningful_edit = True
                break
    if meaningful_edit:
        score += 5
        signals.append("+5: meaningful file edit or revert (>=1 line changed)")
    elif _any_event(events, "file.edited"):
        signals.append(
            "+0: file.edited events present but all were no-op (0 lines "
            "changed) — not credited"
        )
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
    prompt_judgements: dict[str, Any] | None = None,
) -> DimensionScore:
    """Score prompt quality as the AVERAGE of the LAST 3 turns.

    Primary path (P0-1): when ``prompt_judgements`` is provided, average the
    LLM-judge scores keyed by prompt text. The judge scores against a
    4-axis rubric (specificity, constraint, engagement, verifiability)
    and is cached by SHA-256 of the prompt + mission identity, so replays
    are byte-identical.

    Fallback path: when no judgements are supplied (LLM disabled, cache
    miss without a client, unit-test environment), fall back to the
    structural heuristic below. This keeps tests hermetic and lets the
    API boot in environments without ``civitas_core``. The fallback is
    NOT a valid measurement instrument — see P0-1 in the audit plan; it
    is preserved only as a graceful degradation path.

    A single judgement with ``score=None`` (LLM call failed, cache cold)
    signals measurement-unavailable: the dimension returns
    ``score=None`` so the total reflects only the dimensions that could
    actually be scored.

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

    # Collect the trailing 3 prompts up-front so both the judge path and
    # the keyword fallback share the same windowing rule.
    all_prompts: list[str] = []
    for turn in agent_turns:
        if not isinstance(turn, dict):
            continue
        prompt = turn.get("user_prompt", turn.get("prompt", "")) or ""
        if prompt:
            all_prompts.append(prompt)

    if prompt_judgements is not None:
        # Primary path: average the LLM-judge scores for the trailing 3
        # prompts. The judge produces a per-axis breakdown plus a 0-10
        # integer score; we just average integers here since that's the
        # rubric boundary. A mix of judged + unjudged prompts (e.g. one
        # prompt's cache entry was deleted between calls) falls back to
        # the keyword scorer for the missing prompts — better than
        # silently dropping them.
        tail = all_prompts[-3:]
        if not tail:
            return DimensionScore(
                score=0,
                max_score=max_score,
                signals=["no agent turns with user_prompt — scored 0/10"],
            )
        per_turn_scores: list[int] = []
        any_pending = False
        for p in tail:
            j = prompt_judgements.get(p)
            if j is None:
                signals.append(
                    f"prompt not in judgements lookup (len={len(p)}) — "
                    "skipping (likely cache eviction or batch drift)"
                )
                continue
            j_score = getattr(j, "score", None)
            if j_score is None:
                any_pending = True
                err = getattr(j, "error", "unknown")
                signals.append(
                    f"prompt_quality_pending: judgement.score=None "
                    f"(error={err}) — measurement unavailable"
                )
                continue
            per_turn_scores.append(int(j_score))
            # Surface the per-axis breakdown so the diagnostic narrative
            # downstream can tell the user *what* was weak (not just the
            # aggregate).
            cache_marker = "cache_hit" if getattr(j, "cache_hit", False) else "fresh"
            signals.append(
                f"judge[{cache_marker}] score={j_score}/10 "
                f"specificity={getattr(j, 'specificity', 0):.1f}/2.5 "
                f"constraint={getattr(j, 'constraint', 0):.1f}/2.5 "
                f"engagement={getattr(j, 'engagement', 0):.1f}/2.5 "
                f"verifiability={getattr(j, 'verifiability', 0):.1f}/2.5"
            )
        if any_pending and not per_turn_scores:
            # Every prompt in the tail was unmeasurable — report the
            # dimension as None so the total drops to a max of 90 and
            # the user sees uncertainty rather than a fake number.
            return DimensionScore(
                score=-1,  # sentinel: read by ScoreReport.total to skip
                max_score=max_score,
                signals=signals
                + [
                    "no judgeable prompts in window — dimension excluded "
                    "from total (max becomes 90)"
                ],
            )
        if per_turn_scores:
            avg = round(sum(per_turn_scores) / len(per_turn_scores))
            signals.insert(
                0,
                f"averaged last {len(per_turn_scores)} of "
                f"{len(all_prompts)} prompt(s) via LLM judge → {avg}/{max_score}",
            )
            return DimensionScore(
                score=max(0, min(avg, max_score)),
                max_score=max_score,
                signals=signals,
            )
        # No judgement entries resolved (lookup populated but missed every
        # tail prompt — e.g. cache eviction or batch drift). Fall through to
        # keyword but record explicitly so the user knows the judge was
        # bypassed.
        signals.append(
            f"judge unavailable for all {len(tail)} tail prompt(s); "
            "reverted to keyword fallback"
        )

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

        if not must_include:
            # The mission declared no required-keyword set — award the band
            # by default rather than penalising the supervisor for the
            # author's omission.
            turn_score += 2
            turn_signals.append("+2: no required-keyword set declared (N/A)")
        elif any(kw.lower() in prompt_lower for kw in must_include):
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

    # Use the prompt list collected above (shared between judge and fallback
    # paths). The trailing-3 window matches the judge path.
    if not all_prompts:
        return DimensionScore(
            score=0,
            max_score=max_score,
            signals=["no agent turns with user_prompt — scored 0/10"],
        )

    tail = all_prompts[-3:]
    per_turn = [_score_one_turn(p) for p in tail]
    avg = round(sum(s for s, _ in per_turn) / len(per_turn))

    signals.append(
        f"averaged last {len(tail)} of {len(all_prompts)} prompt(s) "
        f"via keyword fallback → {avg}/{max_score}"
    )
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

    # Operative selection = the LAST ``context.selected`` event that preceded
    # the LAST ``prompt.submitted`` event. That's what the supervisor actually
    # shipped TO the agent; selections made *after* submitting are post-hoc
    # exploration and do not count.
    #
    # If no prompt was submitted, the operative selection is the LAST
    # ``context.selected`` event in the session — the supervisor's most
    # recent considered choice. Previously this branch took the MAX score
    # across all selections, which silently rewarded brute-force enumeration:
    # a user could cycle through every plausible subset and be credited with
    # the best one even though their final selection was wrong. The deliberate
    # final selection is what counts.
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
        # No prompt submitted yet — score the supervisor's LATEST selection.
        # Brute-force enumeration of subsets does not inflate the score; only
        # the most recent deliberate choice counts.
        latest = list(selection_payloads[-1].get("files", []) or [])
        score, required_hit, recommended_hit, discouraged_hit = _score_selection(latest)
        signals.append(
            f"required_hit={required_hit:.2f} recommended_hit={recommended_hit:.2f} "
            f"discouraged_hit={discouraged_hit}"
        )
        signals.append(
            f"no prompt.submitted yet — scored latest of "
            f"{len(selection_payloads)} context.selected event(s) "
            f"({len(latest)} file(s))"
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

    raw_p50 = getattr(manifest, "expected_diff_lines_p50", 20)
    # Plausibility clamp (P1-3): an out-of-band p50 lets a typoed manifest
    # silently shift the entire mission's scoring band. We refuse to honour
    # anything outside [3, 200] and fall back to the default of 20, logging
    # the rejection. `validate_missions.py` is the primary gate; this is the
    # belt-and-braces guard for any manifest that slipped past CI.
    if not isinstance(raw_p50, int) or raw_p50 < 3 or raw_p50 > 200:
        logger.warning(
            "[score] expected_diff_lines_p50={!r} for mission {!r} is outside "
            "the plausible band [3, 200] — falling back to 20.",
            raw_p50,
            getattr(manifest, "id", "<unknown>"),
        )
        signals.append(
            f"manifest p50={raw_p50!r} out of band — using fallback p50=20"
        )
        p50 = 20
    else:
        p50 = raw_p50
    added = diff.added_lines_total()
    removed = diff.removed_lines_total()
    churn = max(added, removed)
    # Zero-churn = the supervisor submitted an empty diff. Correctness will
    # almost certainly be 0/30 in that case, but the minimality dimension
    # used to award 10/10 for "ratio = 0 / p50 = 0 <= 1.0". Treat
    # zero-churn as 0/10 with an explicit signal so the breakdown reads
    # correctly.
    if churn == 0:
        signals.append("churn=0 → 0/10 (no changes submitted)")
        signals.append(
            f"churn=max(added={added}, removed={removed})={churn} p50={p50}"
        )
        return DimensionScore(score=0, max_score=max_score, signals=signals)
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


def _narrative(
    dimensions: dict[str, DimensionScore],
) -> tuple[list[StrengthEntry], list[WeaknessEntry]]:
    """Emit evidence-bearing strength / weakness entries (P0-2).

    Each entry carries:
      * a human-readable ``message`` (the legacy ``str`` shape, preserved
        verbatim so OG-image + sample-report rendering doesn't drift);
      * the ``dimension`` name (so the FE can colour-code by axis);
      * ``evidence_event_ids`` — the list of supervision events that
        drove the dimension's score, populated by
        :func:`_attach_evidence`.
    """
    strengths: list[StrengthEntry] = []
    weaknesses: list[WeaknessEntry] = []

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
        if ds.pending:
            # Measurement-unavailable dimensions are neither strengths nor
            # weaknesses — they are pending. The diagnostic narrative (P2-1)
            # picks them up separately and labels them as such.
            continue
        high_thresh, low_thresh = thresholds.get(dim, (ds.max_score * 0.8, ds.max_score * 0.5))
        label = labels.get(dim, dim)
        evidence = list(ds.evidence_event_ids)
        message = f"{label}: {ds.score}/{ds.max_score}"
        if ds.score >= high_thresh:
            strengths.append(
                StrengthEntry(
                    message=message,
                    dimension=dim,
                    evidence_event_ids=evidence,
                )
            )
        elif ds.score <= low_thresh:
            weaknesses.append(
                WeaknessEntry(
                    message=message,
                    dimension=dim,
                    evidence_event_ids=evidence,
                )
            )

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
    prompt_judgements: dict[str, Any] | None = None,
    completed_mission_ids: list[str] | None = None,
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
    prompt_judgements:
        Optional ``{prompt_text → PromptJudgement}`` lookup pre-computed by
        :class:`app.grading.prompt_judge.PromptJudge`. When provided, the
        prompt-quality dimension uses the LLM-judge scores (cache-hit or
        fresh) instead of the legacy substring scorer (P0-1).
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
        "prompt_quality": _score_prompt_quality(
            agent_turns, manifest, prompt_judgements
        ),
        "context_selection": _score_context_selection(events, manifest),
        "safety": _score_safety(events, validator_results, manifest),
        "diff_minimality": _score_diff_minimality(diff, manifest),
    }
    dimensions: dict[str, DimensionScore] = {name: by_name[name] for name, _ in RUBRIC_DIMENSIONS}

    # Sentinel ``score == -1`` on a dimension means measurement-unavailable
    # (P0-1: prompt_quality cache cold + LLM unavailable). Exclude it from
    # the total AND from the effective maximum so the UI can render
    # "{total} / {effective_max}" honestly.
    pending_dims = [ds for ds in dimensions.values() if ds.score < 0]
    effective_max = max(
        1, 100 - sum(ds.max_score for ds in pending_dims)
    )
    total = sum(ds.score for ds in dimensions.values() if ds.score >= 0)
    total = max(0, min(total, effective_max))

    # Diagnostic narrative (P2-1) — one entry per weakness, with a derived
    # cause and a next-mission recommendation. The narrative consumes the
    # raw signal strings each dimension already emits, so it is fully
    # deterministic (no LLM). When the caller supplies ``completed_mission_ids``,
    # already-attempted missions are excluded from the recommendation set
    # — the user shouldn't be sent back to drills they have done.
    from app.grading.diagnostics import build_feedback_narrative

    feedback_narrative = [
        d.to_dict()
        for d in build_feedback_narrative(
            dimensions=dimensions,
            completed_mission_ids=completed_mission_ids,
        )
    ]

    # P0-2 — stamp evidence event ids onto each dimension BEFORE the
    # narrative is built so strengths/weaknesses inherit the lookup
    # without re-walking the event log.
    _attach_evidence(dimensions, events)

    strengths, weaknesses = _narrative(dimensions)
    missed_failure_mode = not _hidden_tests_passed(test_results, manifest)

    return ScoreReport(
        total=total,
        dimensions=dimensions,
        strengths=strengths,
        weaknesses=weaknesses,
        missed_failure_mode=missed_failure_mode,
        badges_earned=[],  # populated by badges.compute_badges
        feedback_narrative=feedback_narrative,
        effective_max=effective_max,
    )
