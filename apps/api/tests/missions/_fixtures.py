"""Synthetic scoring inputs for the mission acceptance-envelope harness.

These helpers produce the four pieces of input the scoring engine needs
(``ParsedDiff``, supervision events, validator results, test results, and
agent turns) for three replay scenarios per mission:

* :func:`build_unmodified_submission` — supervisor accepts the agent
  patch verbatim and submits with only a perfunctory verification pass.
* :func:`build_ideal_submission`     — supervisor lands a minimal,
  test-bearing fix and exercises the full verification + review loop.
* :func:`build_empty_submission`     — supervisor submits nothing.

The harness is intentionally a *replay* of canned signals: it does not
exercise the Docker sandbox or actually run the agent / test suites.
That contract is documented in :mod:`tests.missions.test_acceptance_envelopes`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult
from app.grading.validators.tests_pass import TestRunResult

# ---------------------------------------------------------------------------
# Container type the harness consumes.
# ---------------------------------------------------------------------------


@dataclass
class ScoringInputs:
    """Inputs to :func:`app.grading.score.compute_score`."""

    diff: ParsedDiff
    events: list[dict[str, Any]]
    validator_results: list[ValidatorResult]
    test_results: list[TestRunResult]
    agent_turns: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Synthesis primitives.
# ---------------------------------------------------------------------------


def _at(base: datetime, offset_s: int) -> str:
    return (base + timedelta(seconds=offset_s)).isoformat()


def _targeted_test_command(manifest: Any) -> str:
    """Return a shell command that contains the mission's targeted-test pattern.

    The score engine's verification rule does a case-insensitive regex match
    on the command string against ``reward_signals.verification.require_targeted_test``.
    Embedding the literal pattern as a `-k` selector guarantees a hit.
    """
    pattern = getattr(
        manifest.reward_signals.verification, "require_targeted_test", None
    ) or "unit"
    return f"pnpm test -- -k {pattern}"


def _ideal_diff_text(manifest: Any) -> str:
    """Synthesize a small, on-target diff that addresses the mission's failure mode.

    The diff touches one canonical expected_file and adds a test that matches
    the first ``regression_test_required`` validator's ``test_globs`` and
    contains at least one of its ``keywords_any_of``. This is enough to:

    * keep ``added_lines_total`` <= the manifest p50 (full diff_minimality
      credit),
    * pass the regression_test_required validator,
    * land "expected_files" into ``changed_paths()`` for the root-cause
      heuristic in final_correctness.
    """
    # Pick the first non-test expected file as the "source fix" target.
    expected_files: list[str] = list(getattr(manifest, "expected_files", []))
    if not expected_files:
        # Fall back to the first required-context file.
        expected_files = list(getattr(manifest.expected_context, "required", []))

    # Locate the regression_test_required validator's globs + keywords.
    test_glob = "tests/regression.test.ts"
    keyword = "regression"
    for v in manifest.validators:
        if getattr(v, "kind", None) == "regression_test_required":
            globs = list(getattr(v, "test_globs", []) or [])
            keywords = list(getattr(v, "keywords_any_of", []) or [])
            if globs:
                test_glob = _glob_to_concrete_path(globs[0])
            if keywords:
                keyword = keywords[0]
            break

    # Pick a source file that is NOT a test file (so the test file is the
    # regression-test addition, not the same path).
    source_file = next(
        (f for f in expected_files if "test" not in f.lower()),
        expected_files[0] if expected_files else "src/index.ts",
    )

    # If no separate test file exists in expected_files, use the validator's
    # glob to derive one.
    if source_file == test_glob:
        test_glob = source_file.replace("/calc.py", "/test_calc.py")

    src_patch = (
        f"diff --git a/{source_file} b/{source_file}\n"
        f"--- a/{source_file}\n"
        f"+++ b/{source_file}\n"
        f"@@ -1,1 +1,2 @@\n"
        f" const placeholder = 1;\n"
        f"+// ideal fix: address the documented failure mode minimally.\n"
    )

    test_patch = (
        f"diff --git a/{test_glob} b/{test_glob}\n"
        f"--- a/{test_glob}\n"
        f"+++ b/{test_glob}\n"
        f"@@ -1,1 +1,3 @@\n"
        f" const placeholder = 1;\n"
        f"+// regression: locks down the {keyword} behaviour.\n"
        f"+it('locks down {keyword} regression', () => {{ /* {keyword} */ }});\n"
    )
    return src_patch + test_patch


def _glob_to_concrete_path(glob: str) -> str:
    """Best-effort: turn a `**/*.test.ts` style glob into a concrete path."""
    # Replace `**/` with an empty segment, `*` with `regression`.
    path = glob.replace("**/", "")
    path = path.replace("*", "regression")
    return path


# ---------------------------------------------------------------------------
# Builders — one per scenario.
# ---------------------------------------------------------------------------


def build_unmodified_submission(manifest: Any, folder: Path) -> ScoringInputs:
    """Supervisor accepts the agent patch verbatim and submits.

    Models the embarrassing-middle case: they ran a targeted test (which
    passed visibly), but did not open the diff, edit anything, or write a
    regression test. The agent patch trips the mission's forbidden_changes
    rules (where applicable) and never adds a test file, so:

    * visible tests pass, hidden tests fail (final_correctness capped at 18);
    * regression_test_required fails;
    * forbidden_changes may pass or fail depending on what the agent did;
    * verification picks up the one targeted-test command (+8) but nothing else.
    """
    diff_text = (folder / "agent_patch.diff").read_text(encoding="utf-8")
    diff = ParsedDiff(diff_text)

    base = datetime.now(UTC) - timedelta(minutes=5)
    required = list(getattr(manifest.expected_context, "required", []))

    events: list[dict[str, Any]] = [
        {
            "event_type": "context.selected",
            "payload": {
                "files": required,
                "logs": [],
                "tests": [],
                "extras": [],
            },
            "occurred_at": _at(base, 0),
        },
        {
            "event_type": "agent.responded",
            "payload": {"turn_index": 0, "response_summary": "applied patch"},
            "occurred_at": _at(base, 20),
        },
        {
            "event_type": "patch.applied",
            "payload": {"turn_index": 0, "files_changed": len(diff.changed_paths())},
            "occurred_at": _at(base, 30),
        },
        {
            "event_type": "command.run",
            "payload": {
                "command": _targeted_test_command(manifest),
                "category": "test",
                "exit_code": 0,
                "duration_ms": 900,
            },
            "occurred_at": _at(base, 60),
        },
    ]

    # Validator results — model what _would_ happen if the runner dispatched
    # them against this diff. We approximate by inspecting the diff text
    # against each mission's forbidden rules; for the rest we rely on simple
    # heuristics (a test file was not added, etc.).
    validator_results = _synthesize_validators_for_agent_patch(diff, manifest, folder)

    test_results = [
        TestRunResult(suite="unit", exit_code=0, stdout="", stderr="", passed=5),
        TestRunResult(suite="hidden", exit_code=1, stdout="", stderr="", failed=3),
    ]

    agent_turns = [
        {
            "turn_index": 0,
            "user_prompt": "please fix the bug",
            "selected_context": {"files": required},
            "agent_response": "ok",
        }
    ]

    return ScoringInputs(
        diff=diff,
        events=events,
        validator_results=validator_results,
        test_results=test_results,
        agent_turns=agent_turns,
    )


def build_ideal_submission(manifest: Any, folder: Path) -> ScoringInputs:
    """Supervisor delivers a minimal fix + regression test + full review loop."""
    diff_text = _ideal_diff_text(manifest)
    diff = ParsedDiff(diff_text)

    base = datetime.now(UTC) - timedelta(minutes=10)
    required = list(getattr(manifest.expected_context, "required", []))
    recommended = list(getattr(manifest.expected_context, "recommended", []))

    # Compose a strong prompt that hits prompt_quality's must_include +
    # bonus_keywords + scope phrase + test/regression mention.
    must_include = list(manifest.reward_signals.prompt_quality.must_include_any) or [
        "root cause"
    ]
    bonus = list(manifest.reward_signals.prompt_quality.bonus_keywords)[:3]
    prompt_text = (
        "Reproduce the failure mode locally first, then deliver the "
        f"minimal fix that addresses the {must_include[0]}. "
        f"Add a regression test that locks down the behaviour. "
        f"Do not modify unrelated files. "
        f"Keywords: {' '.join(bonus)} {must_include[0]}."
    )

    events: list[dict[str, Any]] = [
        {
            "event_type": "context.selected",
            "payload": {
                "files": required + recommended,
                "logs": [],
                "tests": [],
                "extras": [],
            },
            "occurred_at": _at(base, 0),
        },
        {
            "event_type": "prompt.submitted",
            "payload": {
                "turn_index": 0,
                "text": prompt_text,
                "char_count": len(prompt_text),
                "intent": "fix",
                "keyword_hits": [must_include[0]],
            },
            "occurred_at": _at(base, 30),
        },
        {
            "event_type": "agent.responded",
            "payload": {"turn_index": 0, "response_summary": "applied patch"},
            "occurred_at": _at(base, 60),
        },
        {
            "event_type": "patch.applied",
            "payload": {"turn_index": 0, "files_changed": len(diff.changed_paths())},
            "occurred_at": _at(base, 70),
        },
        {
            "event_type": "diff.opened",
            "payload": {"path": diff.changed_paths()[0]},
            "occurred_at": _at(base, 90),
        },
        {
            "event_type": "file.edited",
            "payload": {
                "path": diff.changed_paths()[0],
                "added": 2,
                "removed": 0,
                "source": "user",
            },
            "occurred_at": _at(base, 110),
        },
        {
            "event_type": "prompt.submitted",
            "payload": {
                "turn_index": 1,
                "text": "Revise — narrow the change to just the failing branch.",
                "intent": "revise",
            },
            "occurred_at": _at(base, 130),
        },
        {
            "event_type": "command.run",
            "payload": {
                "command": _targeted_test_command(manifest),
                "category": "test",
                "exit_code": 0,
                "duration_ms": 1100,
            },
            "occurred_at": _at(base, 160),
        },
        {
            "event_type": "command.run",
            "payload": {
                "command": "pnpm typecheck",
                "category": "typecheck",
                "exit_code": 0,
                "duration_ms": 800,
            },
            "occurred_at": _at(base, 170),
        },
        {
            "event_type": "command.run",
            "payload": {
                "command": "pnpm lint",
                "category": "lint",
                "exit_code": 0,
                "duration_ms": 400,
            },
            "occurred_at": _at(base, 180),
        },
    ]

    # In the ideal case every validator passes (the supervisor wrote a real
    # regression test, didn't touch forbidden files, etc.).
    validator_results = [
        ValidatorResult(kind="forbidden_changes", passed=True),
        ValidatorResult(kind="diff_scope", passed=True),
        ValidatorResult(
            kind="regression_test_required",
            passed=True,
            evidence=[{"check": "keyword_present", "hit_keyword": "regression"}],
        ),
        ValidatorResult(kind="no_skipped_tests", passed=True),
        ValidatorResult(kind="no_new_dependencies", passed=True),
        ValidatorResult(kind="no_secrets_exposed", passed=True),
    ]

    test_results = [
        TestRunResult(suite="unit", exit_code=0, stdout="", stderr="", passed=12),
        TestRunResult(suite="hidden", exit_code=0, stdout="", stderr="", passed=8),
    ]

    agent_turns = [
        {
            "turn_index": 0,
            "user_prompt": prompt_text,
            "selected_context": {"files": required},
            "agent_response": "ok",
        }
    ]

    return ScoringInputs(
        diff=diff,
        events=events,
        validator_results=validator_results,
        test_results=test_results,
        agent_turns=agent_turns,
    )


def build_empty_submission(manifest: Any, folder: Path) -> ScoringInputs:
    """Supervisor submits an empty diff with no review activity."""
    diff = ParsedDiff("")

    # No events, no agent turns, no validator passes, no green tests.
    validator_results = [
        ValidatorResult(kind="forbidden_changes", passed=False),
        ValidatorResult(kind="regression_test_required", passed=False),
        ValidatorResult(kind="no_new_dependencies", passed=False),
    ]
    test_results = [
        TestRunResult(suite="unit", exit_code=1, stdout="", stderr="", failed=5),
        TestRunResult(suite="hidden", exit_code=1, stdout="", stderr="", failed=5),
    ]

    return ScoringInputs(
        diff=diff,
        events=[],
        validator_results=validator_results,
        test_results=test_results,
        agent_turns=[],
    )


# ---------------------------------------------------------------------------
# Validator synthesis for the unmodified scenario.
# ---------------------------------------------------------------------------


def _synthesize_validators_for_agent_patch(
    diff: ParsedDiff,
    manifest: Any,
    folder: Path,
) -> list[ValidatorResult]:
    """Approximate what each declared validator would say about the agent diff.

    We deliberately keep this lightweight — running the real validator stack
    would be a separate (and slower) integration target. For envelope
    self-tests we only need the high-impact verdicts:

    * ``forbidden_changes`` — run the actual ``validate_forbidden_changes``
      against the on-disk rules file, since each mission's rules are
      hand-tuned to trip on its agent patch.
    * ``regression_test_required`` — fails (agent never adds a test file).
    * ``no_new_dependencies`` — fails iff the diff touches package.json /
      pyproject.toml / requirements.
    * ``diff_scope`` — fails when the patch hits a discouraged path.
    """
    results: list[ValidatorResult] = []

    for v in manifest.validators:
        kind = getattr(v, "kind", None)
        if kind == "forbidden_changes":
            results.append(_run_forbidden_changes(diff, v, folder))
        elif kind == "regression_test_required":
            # Agent patches never add tests in our content set.
            results.append(
                ValidatorResult(
                    kind="regression_test_required",
                    passed=False,
                    violations=["no regression test added"],
                )
            )
        elif kind == "no_new_dependencies":
            results.append(_run_no_new_deps(diff))
        elif kind == "no_skipped_tests":
            results.append(ValidatorResult(kind="no_skipped_tests", passed=True))
        elif kind == "no_secrets_exposed":
            results.append(ValidatorResult(kind="no_secrets_exposed", passed=True))
        elif kind == "diff_scope":
            results.append(ValidatorResult(kind="diff_scope", passed=True))
        else:
            results.append(ValidatorResult(kind=str(kind), passed=True))

    # Always include a no_new_dependencies result so the safety dimension
    # has something to read even when the manifest didn't declare one.
    if not any(r.kind == "no_new_dependencies" for r in results):
        results.append(_run_no_new_deps(diff))

    return results


def _run_forbidden_changes(
    diff: ParsedDiff, validator: Any, folder: Path
) -> ValidatorResult:
    """Invoke the real forbidden_changes validator with a permissive fs_reader.

    The agent patch we ship is the canonical one — if it trips a rule, it'll
    do so via ``regex_present_in_diff``. ``regex_absent`` rules need the
    live file content; we synthesise the original (pre-patch) content by
    returning a string that contains the rule's pattern (so a benign patch
    is treated as "still there"). The agent patches are written so that any
    file deletion is also visible in the diff anyway.
    """
    from app.grading.validators.forbidden import validate_forbidden_changes

    rules_path = folder / validator.rules_file

    def _fs_reader(path: str) -> str | None:
        # Return a content blob that contains every expected pattern so the
        # `regex_absent` rules can be satisfied by default. The agent patch
        # itself only ever tweaks lines via `regex_present_in_diff` rules.
        # We seed common patterns the missions check for so this stays a
        # permissive sentinel: each mission's `regex_absent` rule is testing
        # whether the export still exists, and our content patches never
        # remove the exports themselves.
        return (
            "export function requireAuth(req, res, next) {}\n"
            "export function serializeUser(u) {}\n"
            "POST /api/submissions\n"
            "def calculate_price(qty, unit):\n"
            "export function assertOwnerOrAdmin(req, res, next) {}\n"
            "def format_report_ts(ts, tz):\n"
            "def claim_job(session, job_id):\n"
        )

    return validate_forbidden_changes(diff, _fs_reader, rules_path)


def _run_no_new_deps(diff: ParsedDiff) -> ValidatorResult:
    """Lightweight no_new_dependencies: fails if the diff touches a manifest."""
    dep_files = {
        "package.json",
        "pnpm-lock.yaml",
        "requirements.txt",
        "pyproject.toml",
        "backend/pyproject.toml",
        "backend/package.json",
    }
    changed = set(diff.changed_paths())
    touched_deps = changed & dep_files
    return ValidatorResult(
        kind="no_new_dependencies",
        passed=not touched_deps,
        violations=(
            [f"dependency manifest touched: {sorted(touched_deps)}"] if touched_deps else []
        ),
    )
