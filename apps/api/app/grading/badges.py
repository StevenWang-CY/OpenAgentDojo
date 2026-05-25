"""Badge awarding (plan §11.4).

``award`` evaluates the 6 MVP badge rules against a graded submission, inserts
new rows into ``user_badges`` for any badge the user hasn't already earned, and
returns the badge IDs awarded *this run*.

Pure-function ``compute_badges`` is kept as a convenience for tests that don't
have a DB session — it evaluates the same rules without persisting.

Badge catalog IDs (kept in sync with ``alembic/versions/0002_seed_badges.py``):

- ``regression-test-writer``
- ``security-aware-reviewer``
- ``agent-skeptic``
- ``minimal-diff``
- ``concurrency-debugger``
- ``api-contract-guardian``
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.grading.score import ScoreReport
from app.grading.validators.base import ValidatorResult
from app.grading.validators.tests_pass import TestRunResult
from app.models.badge import Badge
from app.models.user_badge import UserBadge

BadgeId = str

REGRESSION_WRITER = "regression-test-writer"
SECURITY_AWARE = "security-aware-reviewer"
AGENT_SKEPTIC = "agent-skeptic"
MINIMAL_DIFF = "minimal-diff"
CONCURRENCY_DEBUGGER = "concurrency-debugger"
API_CONTRACT_GUARDIAN = "api-contract-guardian"

REVISE_INTENTS = {"revise", "narrow", "test"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def award(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID,
    mission_id: str,
    score_report: ScoreReport,
    validator_results: list[ValidatorResult],
    test_results: list[TestRunResult],
    events: list[dict[str, Any]],
    manifest: Any | None = None,
) -> list[BadgeId]:
    """Evaluate badges and persist newly-earned ones to ``user_badges``.

    Returns the full list of badges earned for this submission (not just the
    newly-persisted ones). Skips persistence if ``user_id`` is None (e.g.
    headless ``check_missions`` runs).
    """
    earned = compute_badges(
        score_report=score_report,
        validator_results=validator_results,
        test_results=test_results,
        events=events,
        mission_id=mission_id,
        manifest=manifest,
    )
    if not earned or user_id is None:
        return earned

    # Filter against existing user_badges to keep the insert idempotent.
    existing_ids: set[str] = set(
        (await db.execute(select(UserBadge.badge_id).where(UserBadge.user_id == user_id)))
        .scalars()
        .all()
    )

    # Filter against the badges catalog so we don't FK-fail on a missing row.
    catalog_ids: set[str] = set((await db.execute(select(Badge.id))).scalars().all())

    inserted = 0
    for badge_id in earned:
        if badge_id in existing_ids:
            continue
        if catalog_ids and badge_id not in catalog_ids:
            logger.warning("[badges] badge {} not in catalog — skipping insert", badge_id)
            continue
        db.add(
            UserBadge(
                user_id=user_id,
                badge_id=badge_id,
                session_id=session_id,
            )
        )
        inserted += 1
    if inserted:
        await db.flush()

    return earned


def compute_badges(
    score_report: ScoreReport,
    validator_results: list[ValidatorResult],
    test_results: list[TestRunResult],
    events: list[dict[str, Any]],
    mission_id: str | None = None,
    manifest: Any | None = None,
) -> list[BadgeId]:
    """Pure-function badge evaluation — no DB."""
    earned: list[BadgeId] = []
    dims = score_report.dimensions

    # ---- regression-test-writer --------------------------------------
    if _badge_regression_writer(validator_results, manifest):
        earned.append(REGRESSION_WRITER)

    # ---- security-aware-reviewer -------------------------------------
    if _badge_security_aware(validator_results, events):
        earned.append(SECURITY_AWARE)

    # ---- agent-skeptic -----------------------------------------------
    if _badge_agent_skeptic(events):
        earned.append(AGENT_SKEPTIC)

    # ---- minimal-diff -------------------------------------------------
    # Compare against ``max_score`` (not a literal 5) so the badge tracks
    # whatever weight the rubric assigns ``diff_minimality`` — the rubric
    # rebalanced from max=5 to max=10, and pinning the literal silently
    # broke the badge until ``DimensionScore.score`` happened to be 5.
    minimality = dims.get("diff_minimality")
    correctness = dims.get("final_correctness")
    if (
        minimality is not None
        and minimality.score >= minimality.max_score
        and correctness is not None
        and correctness.score >= 24
    ):
        earned.append(MINIMAL_DIFF)

    # ---- concurrency-debugger (mission-scoped) -----------------------
    if mission_id == "async-race-condition":
        # Use the canonical hidden-suite predicate so a manifest that
        # declares custom hidden suite names (via hidden_tests.suites)
        # is treated consistently with the rest of the score engine.
        # Local import to break the runner ⇄ badges circular dep.
        from app.grading.runner import is_hidden_suite

        hidden = [r for r in test_results if is_hidden_suite(manifest, r.suite)]
        if hidden and all(r.exit_code == 0 and r.failed == 0 for r in hidden):
            earned.append(CONCURRENCY_DEBUGGER)

    # ---- api-contract-guardian (mission-scoped) ----------------------
    if mission_id == "api-contract-drift":
        regression = _find_validator(validator_results, "regression_test_required")
        if regression is not None and regression.passed:
            earned.append(API_CONTRACT_GUARDIAN)

    return earned


# ---------------------------------------------------------------------------
# Per-badge rule helpers
# ---------------------------------------------------------------------------


def _badge_regression_writer(
    validator_results: list[ValidatorResult],
    manifest: Any | None,
) -> bool:
    regression = _find_validator(validator_results, "regression_test_required")
    if regression is None or not regression.passed:
        return False
    # Require that the regression-test validator's keywords match a failure
    # mode-relevant keyword. We look at the evidence "hit_keyword" the
    # validator recorded.
    keyword_hit = ""
    for ev in regression.evidence:
        if not isinstance(ev, dict):
            continue
        if ev.get("hit_keyword"):
            keyword_hit = str(ev["hit_keyword"])
            break

    if not keyword_hit:
        # Validator passed but didn't record a keyword (shouldn't happen for
        # the standard implementation). Be generous and award it anyway.
        return True

    failure_words = _failure_mode_words(manifest)
    if not failure_words:
        return True
    return any(w in keyword_hit.lower() for w in failure_words)


def _failure_mode_words(manifest: Any | None) -> list[str]:
    if manifest is None:
        return []
    fm = getattr(manifest, "failure_mode", None)
    if fm is None:
        return []
    parts: list[str] = []
    for attr in ("id", "title", "description"):
        value = getattr(fm, attr, "") or ""
        if value:
            parts.extend(value.lower().replace("_", " ").split())
    # Strip very short stopwords.
    stop = {"the", "a", "an", "is", "of", "and", "to", "in", "on", "for", "by"}
    return [p.strip(".,:;()\"'") for p in parts if len(p) > 3 and p not in stop]


def _badge_security_aware(
    validator_results: list[ValidatorResult],
    events: list[dict[str, Any]],
) -> bool:
    forbidden = _find_validator(validator_results, "forbidden_changes")
    if forbidden is None or not forbidden.passed:
        return False

    # Need at least one file.reverted that came after a patch.applied event.
    patch_idx = _first_index_of(events, "patch.applied")
    if patch_idx is None:
        return False
    for i, ev in enumerate(events):
        if i <= patch_idx:
            continue
        if ev.get("event_type") == "file.reverted":
            return True
    return False


def _badge_agent_skeptic(events: list[dict[str, Any]]) -> bool:
    # Need: corrective prompt + diff.opened + file.edited after patch.applied.
    has_corrective = False
    for ev in events:
        if ev.get("event_type") != "prompt.submitted":
            continue
        payload = ev.get("payload") or {}
        intent = (payload.get("intent") or "").lower()
        keyword_hits = payload.get("keyword_hits") or []
        if intent in REVISE_INTENTS:
            has_corrective = True
            break
        if any(str(kw).lower() in REVISE_INTENTS for kw in keyword_hits):
            has_corrective = True
            break
        text = (payload.get("text") or payload.get("prompt") or "").lower()
        if any(word in text for word in ("revise", "narrow", "add a test", "add test")):
            has_corrective = True
            break
    if not has_corrective:
        return False

    if not _any_event(events, "diff.opened"):
        return False

    patch_idx = _first_index_of(events, "patch.applied")
    if patch_idx is None:
        return False
    for i, ev in enumerate(events):
        if i <= patch_idx:
            continue
        if ev.get("event_type") == "file.edited":
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_validator(results: list[ValidatorResult], kind: str) -> ValidatorResult | None:
    for r in results:
        if r.kind == kind:
            return r
    return None


def _first_index_of(events: list[dict[str, Any]], event_type: str) -> int | None:
    for i, ev in enumerate(events):
        if ev.get("event_type") == event_type:
            return i
    return None


def _any_event(events: list[dict[str, Any]], event_type: str) -> bool:
    return any(ev.get("event_type") == event_type for ev in events)


__all__ = [
    "AGENT_SKEPTIC",
    "API_CONTRACT_GUARDIAN",
    "CONCURRENCY_DEBUGGER",
    "MINIMAL_DIFF",
    "REGRESSION_WRITER",
    "SECURITY_AWARE",
    "BadgeId",
    "award",
    "compute_badges",
]
