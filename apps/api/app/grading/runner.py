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
from app.grading.score import ScoreReport, compute_score
from app.grading.validators import ValidatorResult, dispatch
from app.grading.validators.tests_pass import TestRunResult, validate_tests_pass
from app.models.agent_turn import AgentTurn
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.sandbox.types import GradingArtifacts
from app.sessions.events import EventEmitter

DEFAULT_BUDGET_SECONDS = 300
PER_VALIDATOR_TIMEOUT_S = 30

# Persistent thread pool for sync `driver.read_file` shims so we don't spin a
# fresh event loop + thread per call (P1-B19). The pool is shut down by the
# FastAPI lifespan handler so it doesn't leak threads when the API process
# tears down (see ``shutdown_fs_executor`` below).
_FS_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="grading-fs")


def shutdown_fs_executor() -> None:
    """Stop the grading FS thread pool — invoked by the FastAPI lifespan.

    Uses ``cancel_futures=True`` so any work queued but not yet started is
    dropped immediately; in-flight worker calls still finish their current
    iteration but ``wait=False`` keeps shutdown non-blocking. Idempotent — a
    second call after shutdown is a no-op (``ThreadPoolExecutor`` flags itself
    as shutdown).
    """
    _FS_EXECUTOR.shutdown(wait=False, cancel_futures=True)


@dataclass
class GradingResult:
    """Return value of :meth:`GradingRunner.run` — drives Submission insert."""

    session_id: uuid.UUID
    final_diff: str
    visible_test_results: dict[str, Any]
    hidden_test_results: dict[str, Any]
    validator_results: dict[str, Any]
    score_report: dict[str, Any]
    total_score: int
    badges_earned: list[str] = field(default_factory=list)
    submission_id: uuid.UUID | None = None
    status: str = "graded"  # "graded" | "error"
    error: str | None = None

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
    ) -> GradingResult:
        """Execute the grading pipeline with a wall-clock budget.

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
                self._pipeline(db, session, driver, handle, manifest, manifest_folder, emitter),
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

        report: ScoreReport = compute_score(
            diff=parsed,
            events=events,
            validator_results=validator_results,
            test_results=all_test_results,
            manifest=manifest,
            agent_turns=agent_turns,
        )

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
        return GradingResult(
            session_id=session_id,
            final_diff=diff_text,
            visible_test_results={r.suite: r.to_dict() for r in visible_results},
            hidden_test_results={r.suite: r.to_dict() for r in hidden_results},
            validator_results={r.kind: r.to_dict() for r in validator_results},
            score_report=report.to_dict(),
            total_score=report.total,
            badges_earned=list(badge_ids),
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
    ) -> tuple[Submission, GradingResult]:
        """Run the pipeline and persist a ``submissions`` row.

        Used by the submit endpoint and the ``check_missions.py`` CLI.
        """
        result = await self.run(db, session, driver, handle, manifest, manifest_folder)
        emitter = EventEmitter(db=db)

        submission = Submission(
            session_id=result.session_id,
            final_diff=result.final_diff,
            visible_test_results=result.visible_test_results,
            hidden_test_results=result.hidden_test_results,
            validator_results=result.validator_results,
            score_report=result.score_report,
            total_score=result.total_score,
        )
        db.add(submission)
        await db.flush()
        result.submission_id = submission.id

        session.status = "graded"
        session.score = result.total_score
        await db.flush()

        # FE contract requires `score` + `breakdown` (renamed from
        # `total_score` and lifted from score_report.dimensions).
        breakdown: dict[str, Any] = {}
        dims = result.score_report.get("dimensions") or {}
        if isinstance(dims, dict):
            breakdown = {k: dims[k] for k in dims}

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
            },
        )
        await db.commit()
        return submission, result

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


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


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

    hidden_names = _hidden_suite_names(manifest)

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
        if suite.lower() in hidden_names:
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
            "event_type": r.event_type,
            "payload": r.payload,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
        }
        for r in rows
    ]


async def _load_agent_turns(db: AsyncSession, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(AgentTurn)
                .where(AgentTurn.session_id == session_id)
                .order_by(AgentTurn.turn_index)
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
