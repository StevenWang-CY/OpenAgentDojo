#!/usr/bin/env python3
"""Determinism replay harness — exit 1 on any score drift.

The grading engine (``app.grading.score.compute_score``) is documented as a
pure function: byte-for-byte identical inputs must yield byte-for-byte
identical reports. This script asserts that contract by running the engine
``--runs`` times (default 5) against the same inputs and diff'ing the
serialised reports.

Two input modes are supported:

* ``--session-id <uuid>`` — load the session's real ``supervision_events``,
  ``agent_turns``, ``submissions`` row, and mission manifest from Postgres
  and replay them through ``compute_score``. Use this to bisect a real
  customer drift report.
* default (no ``--session-id``)         — replay a hand-crafted fixture
  built from the ``ideal`` envelope of a published mission. This is the
  mode the nightly CI workflow uses; it requires no database side-car
  and asserts that the engine itself is stable across runs and across
  Python interpreter restarts.

Exit codes:
  0  — all runs identical, determinism confirmed
  1  — drift detected (a single diff snippet is printed to stderr)
  2  — invalid invocation / setup failure
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

# Make ``app`` importable regardless of where this script is launched from.
HERE = Path(__file__).resolve()
API_ROOT = HERE.parent.parent
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.grading.score import ScoreReport, compute_score

# ---------------------------------------------------------------------------
# Fixture mode — no DB needed.
# ---------------------------------------------------------------------------


def _load_fixture_inputs(mission_id: str | None) -> tuple[Any, Any]:
    """Build deterministic inputs from the ``ideal`` envelope of a real mission.

    Picks the requested mission (default: ``auth-cookie-expiration`` —
    mission 01, our reference fixture) and runs the same builder the
    acceptance-envelope tests use, so the determinism harness exercises the
    same code paths as CI.

    Returns ``(inputs, manifest)`` ready to feed to ``compute_score``.
    """
    # Make tests/missions importable when running as a script (CI path).
    tests_root = API_ROOT / "tests"
    if str(tests_root) not in sys.path:
        sys.path.insert(0, str(tests_root))

    from app.config import get_settings
    from app.missions.loader import MissionLoader
    from tests.missions._fixtures import build_ideal_submission

    target_id = mission_id or "auth-cookie-expiration"

    get_settings.cache_clear()
    settings = get_settings()
    loader = MissionLoader(settings.missions_root)
    loaded = next((m for m in loader.scan() if m.manifest.id == target_id), None)
    if loaded is None:
        raise SystemExit(
            f"FAIL fixture mission {target_id!r} not found under "
            f"{settings.missions_root}. Available: "
            f"{[m.manifest.id for m in loader.scan()]}"
        )

    inputs = build_ideal_submission(loaded.manifest, loaded.folder)
    return inputs, loaded.manifest


def _run_fixture(inputs: Any, manifest: Any) -> ScoreReport:
    return compute_score(
        diff=inputs.diff,
        events=inputs.events,
        validator_results=inputs.validator_results,
        test_results=inputs.test_results,
        manifest=manifest,
        agent_turns=inputs.agent_turns,
    )


# ---------------------------------------------------------------------------
# Session-id mode — pulls from Postgres.
# ---------------------------------------------------------------------------


async def _load_db_inputs(session_id: uuid.UUID) -> tuple[Any, Any]:
    """Reconstruct ``compute_score`` inputs from a real session row.

    Returns ``(inputs_dict_keyed_for_compute_score, manifest)``.
    """
    # Imports are local so the fixture mode never touches DB code paths.
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.session import get_db_engine, get_session_factory
    from app.grading.diff import ParsedDiff
    from app.grading.validators.base import ValidatorResult
    from app.grading.validators.tests_pass import TestRunResult
    from app.missions.loader import MissionLoader
    from app.models.agent_turn import AgentTurn
    from app.models.session import SessionRow
    from app.models.submission import Submission
    from app.models.supervision_event import SupervisionEvent

    engine = get_db_engine()
    SessionMaker = get_session_factory(engine)

    async with SessionMaker() as db:
        sess: SessionRow | None = (
            await db.execute(select(SessionRow).where(SessionRow.id == session_id))
        ).scalar_one_or_none()
        if sess is None:
            raise SystemExit(f"FAIL session {session_id} not found in database")

        sub: Submission | None = (
            await db.execute(select(Submission).where(Submission.session_id == session_id))
        ).scalar_one_or_none()
        if sub is None:
            raise SystemExit(
                f"FAIL session {session_id} has no submission yet — "
                "determinism replay requires a graded session."
            )

        events_rows = (
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
        events = [
            {
                "event_type": r.event_type,
                "payload": r.payload,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            }
            for r in events_rows
        ]

        turn_rows = (
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
        agent_turns = [
            {
                "turn_index": r.turn_index,
                "user_prompt": r.user_prompt,
                "selected_context": r.selected_context,
                "agent_response": r.agent_response,
            }
            for r in turn_rows
        ]

        diff = ParsedDiff(sub.final_diff or "")

        # Validator + test reconstructions: the submission row stores
        # serialised dicts; we materialise them back as dataclasses so the
        # signature matches the production runner. Accepts both the legacy
        # dict-by-suite shape and the current list-of-dicts shape (the
        # grading runner switched from one to the other for contract
        # alignment — see ``apps/api/app/grading/runner.py``).
        def _as_entry_iter(payload: Any) -> list[dict[str, Any]]:
            if isinstance(payload, list):
                return [p for p in payload if isinstance(p, dict)]
            if isinstance(payload, dict):
                return [
                    {**p, "suite": p.get("suite", k) if isinstance(p, dict) else k}
                    for k, p in payload.items()
                    if isinstance(p, dict)
                ]
            return []

        validator_results = [
            ValidatorResult(
                kind=v.get("kind", "unknown"),
                passed=bool(v.get("passed", False)),
                violations=list(v.get("violations", []) or []),
                evidence=list(v.get("evidence", []) or []),
                penalty=int(v.get("penalty", 0) or 0),
            )
            for v in _as_entry_iter(sub.validator_results)
        ]

        test_results: list[TestRunResult] = []
        for payload_set in (sub.visible_test_results, sub.hidden_test_results):
            for entry in _as_entry_iter(payload_set):
                suite_name = entry.get("suite") or "unknown"
                test_results.append(
                    TestRunResult(
                        suite=str(suite_name),
                        exit_code=int(entry.get("exit_code", -1)),
                        stdout=str(entry.get("stdout", "") or ""),
                        stderr=str(entry.get("stderr", "") or ""),
                        passed=int(entry.get("passed", 0) or 0),
                        failed=int(entry.get("failed", 0) or 0),
                        skipped=int(entry.get("skipped", 0) or 0),
                    )
                )

        settings = get_settings()
        loader = MissionLoader(settings.missions_root)
        loaded = next((m for m in loader.scan() if m.manifest.id == sess.mission_id), None)
        if loaded is None:
            raise SystemExit(
                f"FAIL mission {sess.mission_id!r} not found under {settings.missions_root}"
            )

    await engine.dispose()

    inputs = {
        "diff": diff,
        "events": events,
        "validator_results": validator_results,
        "test_results": test_results,
        "agent_turns": agent_turns,
    }
    return inputs, loaded.manifest


def _run_db(inputs: dict[str, Any], manifest: Any) -> ScoreReport:
    return compute_score(
        diff=inputs["diff"],
        events=inputs["events"],
        validator_results=inputs["validator_results"],
        test_results=inputs["test_results"],
        manifest=manifest,
        agent_turns=inputs["agent_turns"],
    )


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _serialise(report: ScoreReport) -> str:
    """Stable JSON of the report — what we diff between runs."""
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def _diff(a: str, b: str) -> str:
    return "".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile="run-1",
            tofile="run-N",
            n=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay compute_score N times and assert byte-identical output."
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help=(
            "Optional session UUID. When set, reads events / turns / submission "
            "from the database. When omitted, uses the fixture mode (no DB)."
        ),
    )
    parser.add_argument(
        "--mission-id",
        type=str,
        default=None,
        help="Fixture mode only — which mission's ideal envelope to replay.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="How many times to invoke compute_score. Default: 5.",
    )
    args = parser.parse_args()

    if args.runs < 2:
        print("FAIL --runs must be >= 2 to detect drift", file=sys.stderr)
        return 2

    if args.session_id:
        try:
            sid = uuid.UUID(args.session_id)
        except ValueError:
            print(f"FAIL invalid session UUID: {args.session_id!r}", file=sys.stderr)
            return 2
        inputs, manifest = asyncio.run(_load_db_inputs(sid))
        mode = f"session={sid}"
        run_one = lambda: _run_db(inputs, manifest)  # noqa: E731
    else:
        fixture_inputs, manifest = _load_fixture_inputs(args.mission_id)
        mode = f"fixture-mission={manifest.id}"
        run_one = lambda: _run_fixture(fixture_inputs, manifest)  # noqa: E731

    first = _serialise(run_one())
    print(f"[determinism] mode={mode} runs={args.runs}")
    print(f"[determinism] run 1 total={json.loads(first)['total']}")

    for i in range(2, args.runs + 1):
        nth = _serialise(run_one())
        if nth != first:
            print(
                f"FAIL determinism drift detected on run {i}:",
                file=sys.stderr,
            )
            print(_diff(first, nth), file=sys.stderr)
            return 1
        print(f"[determinism] run {i} identical")

    print(f"PASS {args.runs} runs produced byte-identical score reports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
