"""Grading pipeline orchestrator (M5).

``GradingRunner`` owns the full submit-time grading pipeline:

1. Set session ``status='submitting'`` + emit ``submission.requested`` event.
2. Call ``driver.freeze_and_grade(handle, mission)`` to capture the diff and
   run visible + hidden tests.
3. Dispatch each manifest validator via :mod:`app.grading.validators` —
   each wrapped in ``asyncio.wait_for`` + try/except so one slow/bad
   validator never crashes the run.
4. Compute the :class:`ScoreReport` via :func:`compute_score`.
5. Award badges via :func:`app.grading.badges.award`.
6. Persist a ``submissions`` row.
7. Set session ``status='graded'`` + emit ``submission.graded`` event.
8. Enforce a wall-clock budget (default 300s); on timeout set
   ``status='error'`` and emit ``submission.failed``.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.grading.badges import award as award_badges
from app.grading.diff import ParsedDiff
from app.grading.dimensions import RUBRIC_DIMENSIONS
from app.grading.prompt_judge import (
    PromptJudge,
    PromptJudgeContext,
)
from app.grading.prompt_judge import (
    PromptJudgement as JudgementResult,
)
from app.grading.score import GAVE_UP_SCORE_CAP, ScoreReport, apply_score_cap, compute_score
from app.grading.validators import ValidatorResult, dispatch
from app.grading.validators.tests_pass import TestRunResult, validate_tests_pass
from app.models.agent_turn import AgentTurn
from app.models.prompt_judgement import PromptJudgement as PromptJudgementRow
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.sandbox.types import GradingArtifacts
from app.sessions.events import EventEmitter, drain_pending_publishes

DEFAULT_BUDGET_SECONDS = 300
PER_VALIDATOR_TIMEOUT_S = 30

# §11.2 rubric — the seven dimensions in canonical order with their MAX
# scores. Re-exported from :mod:`app.grading.dimensions` so that the runner,
# the score engine, and the profile aggregator all share a single source of
# truth (a renamed or re-weighted dimension can no longer drift between
# layers). Defaulting against this table guarantees ``submission.graded``
# always carries all seven keys (P1-B4) so the FE radar chart never renders a
# hole.
_RUBRIC_DIMENSIONS: tuple[tuple[str, int], ...] = RUBRIC_DIMENSIONS


def _ensure_all_dimensions(
    breakdown: dict[str, Any],
    *,
    session_id: uuid.UUID,
) -> dict[str, Any]:
    """Return a ``breakdown`` dict with every rubric dimension present.

    A score dimension that compute_score didn't produce (e.g. because a
    refactor broke the import path or a custom report stub was injected
    mid-pipeline) is filled in with a zero score, the correct max, and a
    ``dimension_missing`` signal so the FE renders the gap explicitly rather
    than dropping the radar axis. We log a single structured warning so the
    operator knows to investigate; this code path should never run in
    production with the current scoring engine.
    """
    out: dict[str, Any] = {}
    missing: list[str] = []
    for name, max_score in _RUBRIC_DIMENSIONS:
        value = breakdown.get(name)
        if isinstance(value, dict) and "score" in value and "max" in value:
            out[name] = value
        else:
            missing.append(name)
            out[name] = {
                "score": 0,
                "max": max_score,
                "signals": ["dimension_missing"],
            }
    if missing:
        logger.warning(
            "[grader] submission.graded breakdown missing dimensions={} for session={}; "
            "defaulting to zero — investigate scoring engine drift",
            missing,
            session_id,
        )
    return out


# Persistent thread pool for sync `driver.read_file` shims so we don't spin a
# fresh event loop + thread per call (P1-B19). The pool is shut down by the
# FastAPI lifespan handler so it doesn't leak threads when the API process
# tears down (see ``shutdown_fs_executor`` below).
_FS_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="grading-fs")


# Module-level anchor for grading futures whose callers don't otherwise hold
# a strong reference (e.g. fire-and-forget background grader tasks). The
# lifespan handler awaits these on shutdown so an in-flight grade can finish
# committing its submission row + envelope before the process tears down.
# Empty in the common case where grading runs inline inside a FastAPI
# request handler (the request task is the strong reference there).
_ACTIVE_GRADING_TASKS: set[asyncio.Task[Any]] = set()


def register_grading_task(task: asyncio.Task[Any]) -> None:
    """Register an in-flight grading ``asyncio.Task`` for shutdown awaiting.

    Background graders (anything spawned via ``asyncio.create_task`` that
    invokes :meth:`GradingRunner.run` / ``run_and_persist``) MUST register
    here so :func:`await_active_grading_tasks` can drain them on lifespan
    shutdown. Inline graders inside a FastAPI request handler don't need
    to register — the request task already keeps the coroutine pinned.
    """
    _ACTIVE_GRADING_TASKS.add(task)
    task.add_done_callback(_ACTIVE_GRADING_TASKS.discard)


def active_grading_task_count() -> int:
    """Test-friendly snapshot of how many graders are still in flight."""
    return len(_ACTIVE_GRADING_TASKS)


async def await_active_grading_tasks(deadline_seconds: float = 10.0) -> tuple[int, int]:
    """Wait for tracked grading tasks to finish; return (completed, timed_out).

    Used by the FastAPI lifespan to drain background graders before the
    thread pool is torn down. Bounded by ``deadline_seconds`` so a
    wedged grader cannot block process shutdown forever; whatever
    didn't finish in time is reported in the log line the caller emits.
    """
    if not _ACTIVE_GRADING_TASKS:
        return (0, 0)
    pending = set(_ACTIVE_GRADING_TASKS)
    done, still_pending = await asyncio.wait(pending, timeout=deadline_seconds)
    return (len(done), len(still_pending))


def shutdown_fs_executor(*, wait: bool = True) -> None:
    """Stop the grading FS thread pool — invoked by the FastAPI lifespan.

    Default ``wait=True`` blocks until any in-flight worker thread's
    current iteration finishes — the previous ``wait=False`` could
    abandon a partially-flushed file read mid-grade, which the runner
    then surfaced as a confusing ``RuntimeError`` after lifespan exit.
    ``cancel_futures=True`` still drops queued-but-unstarted work so
    shutdown is bounded by the longest in-flight call rather than the
    full queue depth. Idempotent — a second call after shutdown is a
    no-op (``ThreadPoolExecutor`` flags itself as shutdown).
    """
    _FS_EXECUTOR.shutdown(wait=wait, cancel_futures=True)


@dataclass
class GradingResult:
    """Return value of :meth:`GradingRunner.run` — drives Submission insert.

    ``visible_test_results`` / ``hidden_test_results`` / ``validator_results``
    are LISTS so they match the shared-types contract
    (``packages/shared-types/src/api.ts`` declares them as arrays). Each entry
    is a serialised :class:`TestRunResult` / :class:`ValidatorResult` dict.
    """

    session_id: uuid.UUID
    final_diff: str
    visible_test_results: list[dict[str, Any]]
    hidden_test_results: list[dict[str, Any]]
    validator_results: list[dict[str, Any]]
    score_report: dict[str, Any]
    total_score: int
    badges_earned: list[str] = field(default_factory=list)
    submission_id: uuid.UUID | None = None
    status: str = "graded"  # "graded" | "error"
    error: str | None = None
    # P0-4 — when set, the give-up affordance triggered the cap. Persisted to
    # submission.score_cap_reason so the FE can render the chip on the
    # report header and the profile aggregator can exclude this attempt
    # from best-per-mission when an uncapped attempt exists. ``None``
    # means no cap applied (the common path).
    score_cap_reason: str | None = None
    # P0-8 — true iff the producing session was ``proctored`` at grade time.
    # Stamped from ``session.mode`` inside ``_pipeline`` and persisted into
    # ``submissions.verified`` by ``run_and_persist``. Drives the verify
    # envelope, the report-page badge, and the public profile partition.
    verified: bool = False

    def as_submission_data(self) -> dict[str, Any]:
        """Plain dict shape used by ``sessions.submit.submit_session``."""
        return {
            "session_id": self.session_id,
            "final_diff": self.final_diff,
            "visible_test_results": self.visible_test_results,
            "hidden_test_results": self.hidden_test_results,
            "validator_results": self.validator_results,
            "score_report": self.score_report,
            "total_score": self.total_score,
            "score_cap_reason": self.score_cap_reason,
        }


class GradingRunner:
    """Orchestrates the grading pipeline for a single submitted session."""

    def __init__(
        self,
        settings: Any,
        budget_seconds: int = DEFAULT_BUDGET_SECONDS,
    ) -> None:
        self.settings = settings
        self.budget_seconds = budget_seconds

    # ------------------------------------------------------------------
    # Public entry point — full pipeline with timeout + event lifecycle.
    # ------------------------------------------------------------------

    async def run(
        self,
        db: AsyncSession,
        session: SessionRow,
        driver: Any,
        handle: Any,
        manifest: Any,
        manifest_folder: Path,
        manifest_sha256: str,
    ) -> GradingResult:
        """Execute the grading pipeline with a wall-clock budget.

        ``manifest_sha256`` comes from :class:`LoadedMission.manifest_sha256`
        and is the on-disk manifest content hash. A bare
        :class:`MissionManifest` does not carry the hash, so callers MUST
        supply it — it anchors the prompt-judge cache revision and the
        ``Submission.manifest_sha256`` column so an edit to a mission's
        prompts / intents / forbidden rules invalidates stale judgements
        per the determinism contract in CONTEXT.md.

        On timeout, sets the session's status to ``'error'``, emits
        ``submission.failed``, and raises :class:`asyncio.TimeoutError`.
        """
        session_id: uuid.UUID = session.id
        emitter = EventEmitter(db=db)

        # Step 1: emit submission.requested. The status flip to 'submitting'
        # is now done atomically by the submit route (P0-B2), so we no longer
        # mutate session.status here.
        await self._emit(
            emitter,
            session_id,
            "submission.requested",
            {"started_at_iso": _now_iso()},
        )
        # Flush so subscribers can see the event during the pipeline, but do
        # NOT commit — the route's get_db owns the commit (P0-B3).
        await db.flush()

        try:
            return await asyncio.wait_for(
                self._pipeline(
                    db,
                    session,
                    driver,
                    handle,
                    manifest,
                    manifest_folder,
                    emitter,
                    manifest_sha256,
                ),
                timeout=self.budget_seconds,
            )
        except TimeoutError:
            logger.error(
                "[grader] session {} exceeded budget {}s — failing run",
                session_id,
                self.budget_seconds,
            )
            session.status = "error"
            await self._emit(
                emitter,
                session_id,
                "submission.failed",
                {
                    "stage": "grading",
                    "detail": f"exceeded {self.budget_seconds}s budget",
                },
            )
            await db.commit()
            # The route is about to re-raise — drain the queued publishes now
            # so subscribers actually see ``submission.failed`` instead of
            # losing it when the outer get_db dependency aborts (P0-B7).
            await _safe_drain(db)
            raise
        except Exception as exc:
            logger.exception("[grader] session {} pipeline failed: {}", session_id, exc)
            session.status = "error"
            await self._emit(
                emitter,
                session_id,
                "submission.failed",
                {"stage": "pipeline", "detail": str(exc)[:500]},
            )
            await db.commit()
            await _safe_drain(db)
            raise

    # ------------------------------------------------------------------
    # Internal pipeline — no timeout handling at this layer.
    # ------------------------------------------------------------------

    async def _pipeline(
        self,
        db: AsyncSession,
        session: SessionRow,
        driver: Any,
        handle: Any,
        manifest: Any,
        manifest_folder: Path,
        emitter: EventEmitter,
        manifest_sha256: str,
    ) -> GradingResult:
        session_id: uuid.UUID = session.id

        # Step 2: freeze + grade via the driver.
        logger.info("[grader] freezing + grading session {}", session_id)
        artifacts: GradingArtifacts = await driver.freeze_and_grade(
            handle, manifest, manifest_folder=manifest_folder
        )

        diff_text = artifacts.diff or ""
        parsed = ParsedDiff(diff_text)

        visible_results, hidden_results = _split_results(artifacts.test_results, manifest)
        all_test_results = visible_results + hidden_results

        # Emit one `test.run` event per suite so the Timeline reflects what
        # ran at grade time (§5.2 event list).
        for tr in all_test_results:
            await self._emit(
                emitter,
                session_id,
                "test.run",
                {
                    "suite": tr.suite,
                    "passed": tr.passed,
                    "failed": tr.failed,
                    "skipped": tr.skipped,
                    "exit_code": tr.exit_code,
                },
            )

        # Step 3: dispatch validators with fail-soft + per-validator timeout.
        def _fs_reader(path: str) -> str | None:
            return _read_workspace_file_sync(driver, handle, path)

        ctx: dict[str, Any] = {
            "diff": parsed,
            "fs_reader": _fs_reader,
            "manifest_folder": manifest_folder,
            "test_results": all_test_results,
            "manifest": manifest,
        }

        validator_results: list[ValidatorResult] = []
        for v_config in manifest.validators:
            kind = (
                getattr(v_config, "kind", None)
                or (v_config.get("kind") if isinstance(v_config, dict) else None)
                or "unknown"
            )
            try:
                # Run the (sync) validator in a thread under a per-validator
                # timeout so a hung validator never blocks the whole budget.
                vr = await asyncio.wait_for(
                    asyncio.to_thread(dispatch, v_config, ctx),
                    timeout=PER_VALIDATOR_TIMEOUT_S,
                )
            except TimeoutError:
                logger.error(
                    "[grader] validator {} timed out after {}s",
                    kind,
                    PER_VALIDATOR_TIMEOUT_S,
                )
                vr = ValidatorResult(
                    kind=str(kind),
                    passed=False,
                    violations=[f"validator timed out after {PER_VALIDATOR_TIMEOUT_S}s"],
                )
            except Exception as exc:
                logger.error("[grader] validator {} raised: {}", kind, exc, exc_info=True)
                vr = ValidatorResult(
                    kind=str(kind),
                    passed=False,
                    violations=[f"validator error: {exc}"],
                )
            validator_results.append(vr)

        # Always tack on tests_actually_pass so the score engine sees it.
        validator_results.append(validate_tests_pass(all_test_results))

        # Emit one `validator.flag` event per failed validator so the
        # Timeline highlights what tripped during grading (§5.2 event list).
        for vr in validator_results:
            if not vr.passed:
                await self._emit(
                    emitter,
                    session_id,
                    "validator.flag",
                    {
                        "kind": vr.kind,
                        "message": "; ".join(vr.violations[:3])
                        if vr.violations
                        else "validator failed",
                        "penalty": vr.penalty,
                    },
                )

        # Step 4: compute the score (pure function).
        events = await _load_events(db, session_id)
        agent_turns = await _load_agent_turns(db, session_id)

        # Pre-compute LLM-judge scores for prompt_quality (P0-1). The judge
        # is cache-first: replays read from ``prompt_judgements`` and never
        # call the model. ``compute_score`` itself stays synchronous and
        # pure — it just consumes the pre-computed lookup. If the LLM is
        # unavailable on a cold cache, the resulting judgement carries
        # ``score=None`` and the prompt_quality dimension reports pending.
        prompt_judgements = await _build_prompt_judgements(
            db=db,
            manifest=manifest,
            agent_turns=agent_turns,
            manifest_sha256=manifest_sha256,
        )

        # Already-attempted missions for this user — the diagnostic
        # narrative uses this to avoid recommending missions the user
        # has already tried.
        completed_mission_ids = await _load_completed_mission_ids(db=db, user_id=session.user_id)

        report: ScoreReport = compute_score(
            diff=parsed,
            events=events,
            validator_results=validator_results,
            test_results=all_test_results,
            manifest=manifest,
            agent_turns=agent_turns,
            prompt_judgements=prompt_judgements,
            completed_mission_ids=completed_mission_ids,
        )

        # P0-4 — apply give-up cap AFTER compute_score so dimension scores
        # remain honest. The cap only touches ``total`` (and records the
        # uncapped value + reason). If the user gave up but their honest
        # total is already <= 50, ``apply_score_cap`` records the reason
        # without lowering the number — the chip still renders so the
        # report stays honest about the deliberate give-up.
        cap_reason: str | None = None
        if session.gave_up_at is not None:
            apply_score_cap(report, reason="gave_up", cap=GAVE_UP_SCORE_CAP)
            cap_reason = "gave_up"

        # P0-8 — capture the proctored posture as a plain bool so downstream
        # ``run_and_persist`` (and the verify envelope it stamps) sees the
        # exact state of ``session.mode`` at grade time. A future code path
        # that wants to promote a self-study attempt to verified MUST run
        # through this point; the column is otherwise immutable.
        verified = getattr(session, "mode", "self_study") == "proctored"

        # Step 5: award badges (persisted by award()).
        mission_id = getattr(manifest, "id", None) or session.mission_id
        badge_ids = await award_badges(
            db=db,
            user_id=session.user_id,
            session_id=session_id,
            mission_id=mission_id,
            score_report=report,
            validator_results=validator_results,
            test_results=all_test_results,
            events=events,
            manifest=manifest,
        )
        report.badges_earned = list(badge_ids)

        # Step 6: build GradingResult.
        # Lists (not dicts) so the JSON serialisation matches the contract in
        # ``packages/shared-types/src/api.ts``. Each entry includes the
        # ``suite`` / ``kind`` key inline (TestRunResult.to_dict / the same on
        # ValidatorResult already carries it, but we make it explicit so a
        # future shape regression in those serialisers can't silently strip
        # the discriminator the FE filters on).
        return GradingResult(
            session_id=session_id,
            final_diff=diff_text,
            visible_test_results=[{**r.to_dict(), "suite": r.suite} for r in visible_results],
            hidden_test_results=[{**r.to_dict(), "suite": r.suite} for r in hidden_results],
            validator_results=[{**r.to_dict(), "kind": r.kind} for r in validator_results],
            score_report=report.to_dict(),
            total_score=report.total,
            badges_earned=list(badge_ids),
            score_cap_reason=cap_reason,
            verified=verified,
        )

    # ------------------------------------------------------------------
    # Convenience: run + persist a Submission row + emit submission.graded.
    # ------------------------------------------------------------------

    async def run_and_persist(
        self,
        db: AsyncSession,
        session: SessionRow,
        driver: Any,
        handle: Any,
        manifest: Any,
        manifest_folder: Path,
        manifest_sha256: str,
    ) -> tuple[Submission, GradingResult]:
        """Run the pipeline and persist a ``submissions`` row.

        Used by the submit endpoint and the ``check_missions.py`` CLI.
        Tutorial missions short-circuit at the submit layer
        (see :func:`app.sessions.submit.submit_session`); reaching this
        function with ``manifest.kind == "tutorial"`` is a contract
        violation and we surface it loudly rather than silently grading
        the tutorial as a real attempt.
        """
        if getattr(manifest, "kind", "standard") == "tutorial":
            raise RuntimeError(
                "GradingRunner.run_and_persist invoked for a tutorial "
                "mission; use complete_tutorial() instead — tutorial "
                "missions must not be graded through the standard pipeline."
            )
        result = await self.run(
            db, session, driver, handle, manifest, manifest_folder, manifest_sha256
        )
        emitter = EventEmitter(db=db)

        # Pre-allocate the submission id so the P0-11 verification envelope
        # can be computed BEFORE ``db.flush()`` — the envelope's
        # ``submission_id`` is part of the canonical hash, so we cannot rely
        # on a server-assigned UUID. Allocated here (rather than just before
        # the envelope build) so the critical-moments failure log below can
        # carry the same submission_id the row will eventually persist with.
        pre_assigned_id = uuid.uuid4()

        # P0-2 — compute the deterministic critical-moment list off the same
        # event stream the score engine just consumed. Stored in its own
        # column (migration 0012) so the FE can render the post-mortem
        # walkthrough without re-deserialising score_report.
        events = await _load_events(db, result.session_id)
        from app.grading.diagnostics import compute_critical_moments

        # Defensive: a malformed event payload (e.g. a non-dict slipping
        # past the supervision pipeline, or a new event variant the
        # critical-moment heuristics don't yet understand) must NOT take
        # the grade down with it. The submission row is the durable
        # record; degrading ``critical_moments`` to an empty list keeps
        # the report renderable and surfaces the bug to the operator
        # without dropping the grade on the floor.
        try:
            critical_moments = [
                cm.to_dict() for cm in compute_critical_moments(events=events, manifest=manifest)
            ]
        except Exception:  # diagnostic failure must not block grading.
            logger.exception(
                "[grader] critical_moments_computation_failed session_id={} submission_id={}",
                result.session_id,
                pre_assigned_id,
            )
            critical_moments = []

        # P0-11 — build + sign the verification envelope from the same
        # row this insert is about to persist. The envelope is the
        # smallest authoritative bundle a third party needs to verify
        # the grading event; same inputs → same hash on every replay.
        from app.models.user import User
        from app.reports.verification import (
            build_envelope,
            stamp,
            verify_secret,
        )

        user_row = (
            await db.execute(select(User).where(User.id == session.user_id))
        ).scalar_one_or_none()

        # P0 determinism — pin ``graded_at`` to a single tz-aware UTC value
        # rounded to seconds and use it for BOTH the envelope and the
        # persisted ``Submission.created_at``. ``Submission.created_at`` has
        # a Postgres ``server_default=func.now()`` which would otherwise be
        # populated at flush time and diverge from the envelope's
        # ``graded_at`` (which the envelope rounds to seconds in
        # ``_coerce_iso``). An explicit value on the ORM instance wins over
        # the server_default, so the DB row stores the exact second the
        # envelope hashed against. Without this, the verification_hash on
        # disk can NEVER round-trip — every backfill --reseal call would
        # reject every row.
        graded_at = _now_utc().replace(microsecond=0)

        # P0-3 — if the user row vanished between submit and grade (account
        # deletion race, or an old session pointing at a tombstoned user)
        # we skip the envelope entirely and persist NULL hash/sig. The
        # report endpoint already 404s on NULL, so the grade still lands;
        # we just don't issue a verification credential against a missing
        # subject. Logged at WARNING so on-call sees the rare race.
        v_hash: str | None = None
        v_sig: str | None = None
        if user_row is None:
            logger.warning(
                "[grader] verification_envelope_skipped_user_missing "
                "session_id={} submission_id={} user_id={}",
                result.session_id,
                pre_assigned_id,
                getattr(session, "user_id", None),
            )
        else:
            envelope_submission = _EnvelopeSubmission(
                id=pre_assigned_id,
                total_score=result.total_score,
                score_cap_reason=result.score_cap_reason,
                score_report=result.score_report,
                created_at=graded_at,
                verified=result.verified,
            )
            try:
                envelope = build_envelope(
                    submission=envelope_submission,
                    session=session,
                    manifest=manifest,
                    user=user_row,
                )
                v_hash, v_sig = stamp(envelope, verify_secret(self.settings))
            except RuntimeError as exc:
                # No verify secret resolvable — log loudly and persist NULL
                # columns. The /verify endpoint 404s on NULL, so the rest of
                # the report continues to work and a misconfig is visible in
                # the logs without crashing the grade.
                logger.error(
                    "[grader] verification secret resolution failed for session={}: {}",
                    result.session_id,
                    exc,
                )
                v_hash = None
                v_sig = None
            except (AttributeError, TypeError) as exc:
                # Envelope build itself errored on an unexpected shape
                # (e.g. a stale session/manifest fixture missing an
                # attribute the builder reads). Persist NULL hash/sig so
                # the grade still lands; the operator gets a structured
                # log to chase. Mirror the same swallow contract the
                # RuntimeError branch above uses.
                logger.error(
                    "[grader] verification_envelope_build_failed "
                    "session_id={} submission_id={} error={}",
                    result.session_id,
                    pre_assigned_id,
                    exc,
                )
                v_hash = None
                v_sig = None

        submission = Submission(
            id=pre_assigned_id,
            session_id=result.session_id,
            final_diff=result.final_diff,
            visible_test_results=result.visible_test_results,
            hidden_test_results=result.hidden_test_results,
            validator_results=result.validator_results,
            score_report=result.score_report,
            total_score=result.total_score,
            # Anchor to the exact manifest content this submission was
            # graded against (the column was added in migration 0008
            # specifically so replays can detect content drift).
            manifest_sha256=manifest_sha256,
            critical_moments=critical_moments,
            # P0-4 — mirrored from the cap applied in ``_pipeline``. NULL
            # when the user submitted normally; ``'gave_up'`` when the
            # give-up affordance was used. The profile aggregator reads
            # this column to dedupe by mission (best non-gave-up beats
            # any gave-up attempt).
            score_cap_reason=result.score_cap_reason,
            # P0-11 — verification envelope hash + HMAC signature.
            verification_hash=v_hash,
            verification_signature=v_sig,
            # P0-8 — mirror the session's posture at grade time. The
            # envelope above ALSO carries this (via the new
            # ``submission.verified`` read in build_envelope), so both the
            # persisted bool and the canonical hash see the same value.
            verified=result.verified,
            # P0 determinism — explicit ``created_at`` overrides the column's
            # ``server_default=func.now()`` so the row stores the same
            # tz-aware UTC second the envelope hashed against. The verify
            # endpoint and the backfill --reseal script both re-derive the
            # envelope from ``Submission.created_at``; if this value drifts
            # by even one second from what the runner hashed, every replay
            # mismatches and the credential becomes unverifiable.
            created_at=graded_at,
        )
        db.add(submission)
        await db.flush()
        result.submission_id = submission.id

        session.status = "graded"
        session.score = result.total_score
        await db.flush()

        # FE contract requires `score` + `breakdown` (renamed from
        # `total_score` and lifted from score_report.dimensions). The breakdown
        # MUST carry all seven rubric dimensions even if compute_score returned
        # a partial dict (P1-B4) — the FE radar chart assumes the canonical
        # axes are always present and silently hides any missing one.
        raw_dims = result.score_report.get("dimensions") or {}
        dims_in: dict[str, Any] = raw_dims if isinstance(raw_dims, dict) else {}
        breakdown = _ensure_all_dimensions(dims_in, session_id=result.session_id)

        effective_max = result.score_report.get("effective_max")
        await self._emit(
            emitter,
            result.session_id,
            "submission.graded",
            {
                "score": result.total_score,
                "breakdown": breakdown,
                "submission_id": str(submission.id),
                "missed_failure_mode": bool(result.score_report.get("missed_failure_mode", False)),
                "badges_earned": result.badges_earned,
                # Include the denominator so the Timeline and OG-image can
                # render ``score / effective_max`` (e.g. 70/90 when a
                # dimension is pending) without re-fetching the report.
                "effective_max": int(effective_max)
                if isinstance(effective_max, (int, float))
                else 100,
                # P0-2 — the post-mortem walkthrough's critical-moment
                # scrubber relies on these. Including them in the live
                # event lets a subscriber (Timeline, future toast)
                # surface the diagnostic without a roundtrip to
                # ``/reports/{id}``.
                "critical_moments": critical_moments,
            },
        )
        # Structured log so an operator can correlate critical-moment
        # spikes to specific missions / sessions without parsing JSONB.
        if critical_moments:
            logger.info(
                "[grader] session={} critical_moments={} kinds={}",
                result.session_id,
                len(critical_moments),
                [m.get("kind") for m in critical_moments],
            )
        await db.commit()
        return submission, result

    # ------------------------------------------------------------------
    # P0-1 — tutorial completion (no scoring, no submission row)
    # ------------------------------------------------------------------

    async def complete_tutorial(
        self,
        db: AsyncSession,
        session: SessionRow,
        manifest: Any,
    ) -> None:
        """Mark a tutorial session as completed.

        Tutorial missions don't go through the scoring pipeline:
          * no Submission row is persisted (the FE redirects to the
            catalog instead of /report after completion);
          * the user's :attr:`tutorial_completed_at` timestamp is set so
            the // start here banner stops rendering;
          * a ``tutorial.completed`` supervision event is emitted so the
            timeline shows the milestone.

        We still set ``session.status = 'graded'`` (the closest terminal
        state in the existing state-machine) and ``session.score = 100``
        as a sentinel — the FE's tutorial-flow branch never reads the
        score, but back-compat code paths that look up the session see
        a deterministic value.
        """
        if getattr(manifest, "kind", "standard") != "tutorial":
            raise RuntimeError(
                "complete_tutorial called for non-tutorial mission; "
                "route through run_and_persist instead."
            )

        from datetime import UTC, datetime

        from sqlalchemy import update as sa_update

        from app.models.user import User

        emitter = EventEmitter(db=db)
        await self._emit(
            emitter,
            session.id,
            "submission.requested",
            {"started_at_iso": _now_iso(), "tutorial": True},
        )

        now = datetime.now(UTC)
        # Set tutorial completion on the user row. Use a bounded UPDATE so
        # the timestamp lands even when the User instance isn't loaded into
        # this session's identity map.
        await db.execute(
            sa_update(User).where(User.id == session.user_id).values(tutorial_completed_at=now)
        )

        session.status = "graded"
        session.score = 100  # sentinel — FE redirects past the report
        session.completed_at = now
        await db.flush()

        await self._emit(
            emitter,
            session.id,
            "tutorial.completed",
            {"mission_id": session.mission_id, "completed_at_iso": now.isoformat()},
        )
        # NOTE: do NOT commit here. The outer ``get_db`` dependency
        # (apps/api/app/db/session.py) commits exactly once at request
        # boundary; an extra commit inside this method previously made
        # ``tutorial.step_completed`` and ``submission.requested`` visible
        # to subscribers before the rest of the unit of work (sandbox
        # teardown, mission stats counter, etc.) could roll back. Single-
        # commit-per-request is the load-bearing invariant.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _emit(
        self,
        emitter: EventEmitter,
        session_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Forward to EventEmitter so Redis fanout fires after commit too."""
        await emitter.emit(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _safe_drain(db: AsyncSession) -> None:
    """Drain queued ``publish_after_commit`` events without re-raising.

    The grading failure paths re-raise the original exception immediately
    after committing the ``submission.failed`` event, so the outer ``get_db``
    dependency will short-circuit before its own drain runs. We invoke
    ``drain_pending_publishes`` here ourselves so subscribers reliably see
    the failure even though the route is about to abort — but if Redis is
    flaky we swallow the error rather than mask the original pipeline
    exception.
    """
    try:
        await drain_pending_publishes(db)
    except Exception as exc:  # pragma: no cover — telemetry only
        logger.warning("[grader] could not drain failure publishes: {}", exc)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _now_utc() -> Any:
    """Return a tz-aware UTC datetime for the verification envelope.

    Pulled out so the runner doesn't import ``datetime`` at module top
    (the existing pattern is to inline the import where needed). The
    envelope rounds to seconds in :func:`verification._coerce_iso`, so
    the microsecond resolution here is harmless.
    """
    from datetime import UTC, datetime

    return datetime.now(UTC)


@dataclass(slots=True)
class _EnvelopeSubmission:
    """Lightweight stand-in for ``Submission`` while building the envelope.

    The envelope builder only reads a handful of attributes — bundling
    them in a dataclass keeps the runner from constructing a partially-
    initialised ORM row just to call the builder.
    """

    id: uuid.UUID
    total_score: int
    score_cap_reason: str | None
    score_report: dict[str, Any]
    created_at: Any
    # P0-8 — proctored posture mirrored from ``session.mode`` at grade time.
    # ``build_envelope`` reads this and stamps the canonical hash with it
    # so the verify endpoint can re-derive the same hash from the persisted
    # row without a second lookup.
    verified: bool = False


def _hidden_suite_names(manifest: Any) -> set[str]:
    """Resolve the canonical hidden-suite name(s) from the manifest.

    Defaults to ``{"hidden"}`` which matches what both drivers emit today.
    Supports a list under ``manifest.hidden_tests.suites`` for missions that
    ship multiple hidden runners (P1-B18 — substring matching is too loose).
    """
    hidden_cfg = getattr(manifest, "hidden_tests", None)
    if hidden_cfg is None:
        return {"hidden"}
    suites = getattr(hidden_cfg, "suites", None)
    if isinstance(suites, (list, tuple)) and suites:
        return {str(s).lower() for s in suites}
    return {"hidden"}


def is_hidden_suite(manifest: Any, suite_name: str) -> bool:
    """Single predicate shared by the runner and the scoring engine.

    Both ``_split_results`` (driver-side bucketing) and
    ``_hidden_tests_passed`` (score engine) historically used different
    heuristics — the runner did an exact lowercase-set lookup against
    ``manifest.hidden_tests.suites`` while ``score.py`` did a ``"hidden" in
    suite.lower()`` substring check. That meant a suite called e.g.
    ``"e2e-canary"`` declared in the manifest as hidden would bucket as
    hidden by the runner but be invisible to the score engine, silently
    zeroing the hidden-test correctness credit. Funnelling both through this
    one helper keeps them honest.
    """
    if not suite_name:
        return False
    return suite_name.lower() in _hidden_suite_names(manifest)


def _split_results(
    test_results: dict[str, Any] | list[Any] | None,
    manifest: Any | None = None,
) -> tuple[list[TestRunResult], list[TestRunResult]]:
    """Split the artifact ``test_results`` into (visible, hidden).

    Uses the manifest's hidden suite names where available — falling back to
    the literal suite name ``"hidden"`` for back-compat (P1-B18).
    """
    visible: list[TestRunResult] = []
    hidden: list[TestRunResult] = []
    if not test_results:
        return visible, hidden

    items: list[tuple[str, Any]] = []
    if isinstance(test_results, dict):
        items = list(test_results.items())
    else:
        for r in test_results:
            suite = (
                getattr(r, "suite", None) if not isinstance(r, dict) else r.get("suite", "unknown")
            )
            items.append((str(suite), r))

    for suite, value in items:
        tr = _coerce_test_run_result(suite, value)
        if is_hidden_suite(manifest, suite):
            hidden.append(tr)
        else:
            visible.append(tr)
    return visible, hidden


def _coerce_test_run_result(suite: str, value: Any) -> TestRunResult:
    if isinstance(value, TestRunResult):
        return value
    if isinstance(value, dict):
        return TestRunResult(
            suite=str(value.get("suite", suite)),
            exit_code=int(value.get("exit_code", -1)),
            stdout=str(value.get("stdout", "") or ""),
            stderr=str(value.get("stderr", "") or ""),
            passed=int(value.get("passed", 0) or 0),
            failed=int(value.get("failed", 0) or 0),
            skipped=int(value.get("skipped", 0) or 0),
        )
    # Fallback: synthesise a failed result.
    return TestRunResult(
        suite=suite,
        exit_code=-1,
        stdout="",
        stderr=f"unrecognised test result type: {type(value).__name__}",
    )


def _read_workspace_file_sync(driver: Any, handle: Any, path: str) -> str | None:
    """Synchronously read a workspace file for the forbidden_changes validator.

    Validators are sync, so we cannot ``await`` the driver. For the local
    driver we read directly from the handle's workdir; for the docker driver
    we hand off to a persistent ThreadPoolExecutor that drives the coroutine
    on its own private loop (P1-B19).
    """
    workdir = getattr(handle, "workdir", None)

    # Local driver: workdir is a real Path we can read from.
    if workdir is not None and isinstance(workdir, Path) and workdir.exists():
        candidate = workdir / path.lstrip("/").removeprefix("workspace/")
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, IsADirectoryError):
            return None
        except Exception:
            return None

    def _worker() -> str | None:
        import asyncio as _asyncio

        loop = _asyncio.new_event_loop()
        try:
            for candidate in (
                f"/workspace/{path.lstrip('/')}",
                path,
            ):
                try:
                    raw = loop.run_until_complete(driver.read_file(handle, candidate))
                except Exception:  # noqa: S112 — best-effort fallback across candidate paths; missing file is expected
                    continue
                if raw is None:
                    continue
                return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        finally:
            loop.close()
        return None

    fut = _FS_EXECUTOR.submit(_worker)
    try:
        return fut.result(timeout=10)
    except Exception:
        return None


async def _load_events(db: AsyncSession, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(SupervisionEvent)
                .where(SupervisionEvent.session_id == session_id)
                .order_by(SupervisionEvent.occurred_at, SupervisionEvent.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            # P0-2 — event id is required for the critical-moment + evidence
            # heuristics; it used to be dropped here, which made the
            # post-mortem walkthrough's "scroll to this event" affordance
            # impossible to build deterministically.
            "id": r.id,
            "event_type": r.event_type,
            "payload": r.payload,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
        }
        for r in rows
    ]


async def _build_prompt_judgements(  # noqa: PLR0915
    *,
    db: AsyncSession,
    manifest: Any,
    agent_turns: list[dict[str, Any]],
    manifest_sha256: str,
) -> dict[str, JudgementResult]:
    """Cache-first lookup of LLM-judge scores for every prompt in the run.

    Replays don't call the model: the ``prompt_judgements`` table is the
    source of truth (P0-1 determinism contract). On a cold cache, the
    judge calls Claude Haiku 4.5 via the existing AnthropicClient and
    writes the verdict back; subsequent grading runs of the same session
    read from the cache and produce byte-identical totals.

    Returns an empty dict when no prompts exist OR when the LLM stack is
    not available in the runtime (e.g. ``civitas_core`` not installed in
    a CI/dev environment with no warm cache for the prompts). The score
    engine treats an empty lookup as "use the legacy keyword fallback"
    so test envelopes and laptop-mode grading still produce a usable
    number — production environments with civitas installed always
    route through the judge.
    """
    from sqlalchemy.exc import IntegrityError

    from app.agent.llm import _HAS_CIVITAS
    from app.grading.prompt_judge import (
        PROMPT_QUALITY_MAX_SCORE,
        RUBRIC_VERSION,
        compute_cache_key,
        prior_response_sha,
    )

    # Pair each prompt with the agent_response of the *immediately
    # preceding* turn — that's what the judge's "engagement" axis grades
    # against. Two turns whose user-prompt text is identical but whose
    # prior agent responses differ MUST be scored separately, so the
    # dedupe key is (prompt, prior_agent_response), not prompt alone.
    sorted_turns = [t for t in agent_turns if isinstance(t, dict)]
    pairs: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for idx, turn in enumerate(sorted_turns):
        prompt_text = turn.get("user_prompt", turn.get("prompt", "")) or ""
        if not prompt_text:
            # P2 — a turn that lacks BOTH ``user_prompt`` and ``prompt`` is a
            # contract violation upstream (the chat ingestor must always
            # carry the user's text); silently skipping it hides the
            # gap from operators and produces a confusing partial
            # judgement set. Skip behaviour is preserved; we just
            # surface it.
            logger.warning(
                "[grader] grading_turn_missing_prompt turn_index={} turn_id={}",
                idx,
                turn.get("id"),
            )
            continue
        prior: str | None = None
        if idx > 0:
            prior_turn = sorted_turns[idx - 1]
            prior = prior_turn.get("agent_response") or prior_turn.get("response") or None
        key = (prompt_text, prior or "")
        if key in seen:
            continue
        seen.add(key)
        pairs.append((prompt_text, prior))
    if not pairs:
        return {}

    mission_id = str(getattr(manifest, "id", "") or "")
    # Cache revision MUST be the manifest content sha — supplied by the
    # caller from LoadedMission.manifest_sha256. Refuse to silently
    # fall back: anything else re-uses stale judgements after an edit
    # to prompts / intents / forbidden_changes, violating the
    # determinism contract that re-grading after a mission edit
    # produces different numbers.
    if not manifest_sha256:
        raise RuntimeError(
            "_build_prompt_judgements requires a non-empty manifest_sha256 "
            "(from LoadedMission); bare MissionManifest has no content hash."
        )
    mission_rev = manifest_sha256
    expected_files = list(getattr(manifest, "expected_files", []) or [])
    expected_context = getattr(manifest, "expected_context", None)
    required_ctx = (
        list(getattr(expected_context, "required", []) or [])
        if expected_context is not None
        else []
    )
    failure_mode = getattr(manifest, "failure_mode", None)
    failure_title = str(getattr(failure_mode, "title", "") or "") if failure_mode else None

    def _ctx_for(prior: str | None) -> PromptJudgeContext:
        return PromptJudgeContext(
            mission_id=mission_id,
            mission_revision=mission_rev,
            expected_files=expected_files,
            expected_context_required=required_ctx,
            failure_mode_title=failure_title,
            prior_agent_response=prior,
        )

    async def _cache_get(cache_key: str) -> JudgementResult | None:
        row = (
            await db.execute(
                select(PromptJudgementRow).where(PromptJudgementRow.cache_key == cache_key)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return JudgementResult(
            cache_key=row.cache_key,
            score=row.score,
            specificity=row.specificity,
            constraint=row.constraint_axis,
            engagement=row.engagement,
            verifiability=row.verifiability,
            rationale=row.rationale,
            cache_hit=True,
        )

    def _cache_put_factory(prior: str | None):
        async def _cache_put(j: JudgementResult) -> None:
            # Idempotent insert via SAVEPOINT: a racing concurrent grader
            # may have written the same cache_key already; we want to
            # tolerate that without destroying the in-flight grading
            # transaction's other uncommitted writes (supervision events,
            # validator flags, etc.). ``begin_nested()`` wraps the insert
            # in a SAVEPOINT so an IntegrityError only rolls back the
            # savepoint — the outer transaction is preserved.
            row = PromptJudgementRow(
                cache_key=j.cache_key,
                mission_id=mission_id,
                mission_revision=mission_rev,
                prior_agent_response_sha=prior_response_sha(prior),
                rubric_version=RUBRIC_VERSION,
                score=int(j.score if j.score is not None else 0),
                specificity=float(j.specificity),
                constraint_axis=float(j.constraint),
                engagement=float(j.engagement),
                verifiability=float(j.verifiability),
                rationale=j.rationale or "",
            )
            try:
                async with db.begin_nested():
                    db.add(row)
            except IntegrityError as exc:
                logger.debug(
                    "prompt_judgements insert raced (key={}): {}",
                    j.cache_key,
                    exc,
                )
            except Exception as exc:
                # Non-race failure — log loud but do not poison the
                # outer transaction; the savepoint context already
                # rolled back the insert.
                logger.warning(
                    "prompt_judgements insert failed (key={}): {}",
                    j.cache_key,
                    exc,
                )

        return _cache_put

    # Touch the constants so import-only linters don't strip them.
    _ = (PROMPT_QUALITY_MAX_SCORE, compute_cache_key)

    # First pass: cache-only lookup. If every prompt is warm we never need
    # the LLM at all, even in test/CI environments.
    warm: dict[str, JudgementResult] = {}
    cold_pairs: list[tuple[str, str | None]] = []
    for prompt_text, prior in pairs:
        ctx = _ctx_for(prior)
        cache_only = PromptJudge(client=None, cache_get=_cache_get, cache_put=None, enabled=False)
        j = await cache_only.score_one(prompt_text, ctx)
        if j.score is not None:
            warm[prompt_text] = j
        else:
            cold_pairs.append((prompt_text, prior))
    if not cold_pairs:
        return warm

    # Cold prompts remain — only call the model if the LLM stack is wired
    # up. Otherwise return what we have warm (an empty dict means the
    # score engine routes through the keyword fallback for the trailing
    # 3 prompts; a partial dict means the judge path drives the dimension
    # and missing entries surface as ``prompt not in judgements lookup``
    # signals).
    if not _HAS_CIVITAS:
        logger.debug(
            "prompt_judge: civitas_core not available; skipping LLM "
            "precompute for {} cold prompt(s)",
            len(cold_pairs),
        )
        return warm

    for prompt_text, prior in cold_pairs:
        ctx = _ctx_for(prior)
        judge = PromptJudge(cache_get=_cache_get, cache_put=_cache_put_factory(prior))
        result = await judge.score_one(prompt_text, ctx)
        warm[prompt_text] = result
    return warm


async def _load_completed_mission_ids(*, db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Return every mission id this user has already graded.

    Used by the diagnostic narrative so we don't recommend missions the
    user has already attempted. Limited to ``status='graded'`` sessions —
    abandoned/errored attempts shouldn't burn a recommendation.
    """
    rows = (
        await db.execute(
            select(SessionRow.mission_id).where(
                SessionRow.user_id == user_id,
                SessionRow.status == "graded",
            )
        )
    ).all()
    return list({str(row.mission_id) for row in rows if row.mission_id})


async def _load_agent_turns(db: AsyncSession, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(AgentTurn)
                .where(AgentTurn.session_id == session_id)
                # Order by (turn_index, created_at, id) for stability — two
                # turns with the same turn_index would otherwise come back in
                # arbitrary order on Postgres, which lets the scoring engine's
                # "last 3 turns" prompt-quality average drift between runs.
                .order_by(AgentTurn.turn_index, AgentTurn.created_at, AgentTurn.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "turn_index": r.turn_index,
            "user_prompt": r.user_prompt,
            "selected_context": r.selected_context,
            "agent_response": r.agent_response,
        }
        for r in rows
    ]


# Backwards-compatible parser kept for any test that imports it directly.
def _parse_test_run_result(suite_name: str, run_result: Any) -> TestRunResult:
    """Convert a sandbox ``RunResult`` to a ``TestRunResult`` with pass/fail counts."""
    stdout: str = getattr(run_result, "stdout", "") or ""
    stderr: str = getattr(run_result, "stderr", "") or ""
    exit_code: int = getattr(run_result, "exit_code", -1)
    timed_out: bool = bool(getattr(run_result, "timed_out", False))

    passed = failed = skipped = 0
    combined = stdout + "\n" + stderr

    m = re.search(
        r"Tests:\s*(?:(\d+)\s+passed)?[,\s]*(?:(\d+)\s+failed)?[,\s]*(?:(\d+)\s+skipped)?",
        combined,
    )
    if m and (m.group(1) or m.group(2) or m.group(3)):
        passed = int(m.group(1) or 0)
        failed = int(m.group(2) or 0)
        skipped = int(m.group(3) or 0)
    else:
        mp = re.search(r"(\d+)\s+passing", combined)
        mf = re.search(r"(\d+)\s+failing", combined)
        ms = re.search(r"(\d+)\s+pending", combined)
        if mp:
            passed = int(mp.group(1))
        if mf:
            failed = int(mf.group(1))
        if ms:
            skipped = int(ms.group(1))
        if not mp and not mf:
            pp = re.search(r"(\d+)\s+passed", combined)
            pf = re.search(r"(\d+)\s+failed", combined)
            ps = re.search(r"(\d+)\s+skipped", combined)
            if pp:
                passed = int(pp.group(1))
            if pf:
                failed = int(pf.group(1))
            if ps:
                skipped = int(ps.group(1))

    return TestRunResult(
        suite=suite_name,
        exit_code=exit_code if not timed_out else max(exit_code, 1),
        stdout=stdout,
        stderr=stderr,
        passed=passed,
        failed=failed,
        skipped=skipped,
    )
