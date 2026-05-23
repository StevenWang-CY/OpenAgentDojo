#!/usr/bin/env python3
"""Validate every mission manifest under ``/missions``.

Runs the loader's strict Pydantic validation plus the §14.11 authoring
checklist:

  1. ``mission.yaml`` validates against the manifest model (Pydantic).
  2. ``agent_patch.diff`` exists, is a non-empty unified diff. (Application
     against ``initial_commit`` is exercised by the sandbox / runtime, not
     here — but we sanity-check that the file references real diff
     headers so we don't ship an empty file.)
  3. At least one validator declared (the unmodified-patch trip check is
     done by integration tests against the score engine — beyond CLI scope).
  4. ``ideal_solution.md`` exists.
  5. ``expected_context.required`` >= 2 files; ``discouraged`` >= 1 file.
  6. ``reward_signals.prompt_quality.must_include_any`` >= 3 keywords.
  7. ``expected_diff_lines_p50`` is set (>0).
  8. Mission id appears in the catalog seed
     (``apps/api/alembic/versions/0003_seed_missions.py``) — checked when
     the seed file is reachable from ``--root``.

Also confirms that an optional ``acceptance.yaml`` parses against
``MissionAcceptance`` (so envelope drift is caught at content-author time).

Exit codes: 0 = success, 2 = one or more validation failures, 1 = bad CLI input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure ``apps/api`` is on the path so ``app.*`` imports resolve whether this
# script is run from the repo root or from within ``apps/api``.
_SCRIPT_DIR = Path(__file__).resolve().parent
_API_DIR = _SCRIPT_DIR.parent
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

import re

from app.missions.acceptance import load_acceptance
from app.missions.loader import LoadedMission, MissionLoader


def _check_seed_contains(seed_path: Path, mission_id: str) -> bool:
    """Return True if ``mission_id`` appears as a string literal in the seed."""
    if not seed_path.exists():
        return False
    text = seed_path.read_text(encoding="utf-8")
    needles = (f'"{mission_id}"', f"'{mission_id}'")
    return any(n in text for n in needles)


# Substrings whose presence in a brief is OK even if they look like code
# patterns. Author can suppress a true-positive false-flag by adding here
# with a justification comment.
_BRIEF_LEAK_ALLOWLIST: set[str] = set()


# Patterns that, if they appear in *both* the brief AND ideal_solution.md,
# indicate the brief is telegraphing a specific implementation idiom rather
# than describing the symptom. Each pattern is a regex.
#
# The heuristic deliberately scopes narrowly to code-style idioms — bare
# file paths, function names, and API routes are LEGITIMATE context the
# brief is allowed to name (the supervisor needs to know where to look).
# What is NOT legitimate: naming the *shape* of the fix (a specific
# generic type, SQL idiom, language feature) that the supervisor should
# discover by reading the agent's diff.
_LEAK_PATTERN_RES: list[tuple[str, re.Pattern[str]]] = [
    (
        "generic-type",
        # e.g. ``Set<string>``, ``Map<int, str>`` — these are code patterns,
        # not domain vocabulary.
        re.compile(r"[A-Z][A-Za-z]*<[^>]+>"),
    ),
    (
        "sql-idiom",
        # SELECT … UPDATE … WHERE patterns — they name the fix shape.
        re.compile(r"\b(UPDATE|SELECT|INSERT|DELETE)\b[^.\n]*\bWHERE\b", re.IGNORECASE),
    ),
    (
        "cast-syntax",
        # `as any`, `as unknown`, `as Foo` — TypeScript escape hatches.
        re.compile(r"\bas\s+(any|unknown|never)\b"),
    ),
    (
        "ts-ignore",
        re.compile(r"@ts-ignore|@ts-expect-error|# type:\s*ignore"),
    ),
    (
        "method-call-with-args",
        # e.g. ``setLoading(false)`` — naming the exact call shape leaks
        # the fix. Bare ``setLoading()`` or ``setLoading`` alone does not.
        re.compile(r"\b[a-z][A-Za-z]+\([^)]+\)"),
    ),
]


def _find_leak_patterns(text: str) -> set[tuple[str, str]]:
    """Return ``{(pattern_label, matched_substring)}`` for every code-idiom
    match in ``text``."""
    hits: set[tuple[str, str]] = set()
    for label, pat in _LEAK_PATTERN_RES:
        for m in pat.finditer(text):
            tok = m.group(0).strip()
            if len(tok) >= 4:
                hits.add((label, tok))
    return hits


def _check_brief_leak(loaded: LoadedMission) -> list[str]:
    """Flag code-idiom tokens that appear in both ``mission.yaml.brief`` and
    ``ideal_solution.md``. The signal of a leak is a *code pattern* (generic
    type, SQL idiom, cast syntax, method call with args) appearing in both
    surfaces — not file paths or function-name references, which are
    legitimate scoping context the brief is allowed to provide.

    Authors can suppress a false positive by adding the exact substring to
    ``_BRIEF_LEAK_ALLOWLIST`` with a one-line comment explaining why it is
    domain vocabulary rather than a fix-shape leak."""
    brief = (loaded.manifest.brief or "").strip()
    if not brief:
        return []
    ideal_path = loaded.folder / "ideal_solution.md"
    if not ideal_path.exists():
        return []
    ideal = ideal_path.read_text(encoding="utf-8")
    ideal_hits = _find_leak_patterns(ideal)
    brief_hits = _find_leak_patterns(brief)
    leaks: list[str] = []
    for label, tok in sorted(ideal_hits):
        if tok in _BRIEF_LEAK_ALLOWLIST:
            continue
        # Match by substring in either token form — the same code pattern
        # may have been matched at slightly different boundaries by the
        # two regex runs.
        if tok in brief or any(tok in btok for _, btok in brief_hits):
            leaks.append(
                f"brief leaks solution idiom [{label}] {tok!r} — also "
                f"present in ideal_solution.md. Rewrite the brief to name "
                f"the symptom, not the fix, or add to _BRIEF_LEAK_ALLOWLIST "
                f"with a justification."
            )
    return leaks


def _check_one(loaded: LoadedMission, seed_path: Path | None) -> list[str]:  # noqa: PLR0912, PLR0915 — §14.11 checklist is intentionally exhaustive
    """Return a list of human-readable error strings for one mission.

    Tutorial missions (``kind == "tutorial"``) are graded by *completion*,
    not by score, so the scoring-sensitive invariants (prompt-quality
    keyword counts, catalog seed presence, ``ideal_solution.diff`` for
    the three-way report comparison) are exempted. They must still ship
    a valid manifest, an applies-cleanly patch, and an ``ideal_solution.md``
    so the post-mission report has a narrative to render.
    """
    m = loaded.manifest
    errors: list[str] = []
    is_tutorial = getattr(m, "kind", "standard") == "tutorial"

    # §14.11 item 2 — agent.patch_file exists on disk and looks like a diff.
    patch_path = loaded.folder / m.agent.patch_file
    if not patch_path.exists():
        errors.append(f"agent.patch_file not found: {patch_path}")
    else:
        body = patch_path.read_text(encoding="utf-8", errors="replace")
        if "diff --git" not in body and "--- a/" not in body:
            errors.append(f"agent.patch_file does not look like a unified diff: {patch_path}")

    # response_template must also exist on disk.
    resp_path = loaded.folder / m.agent.response_template
    if not resp_path.exists():
        errors.append(f"agent.response_template not found: {resp_path}")

    # Optional intents_file: if declared, it must exist.
    if m.agent.intents_file:
        intents_path = loaded.folder / m.agent.intents_file
        if not intents_path.exists():
            errors.append(f"agent.intents_file not found: {intents_path}")

    # §14.11 item 3 — at least one validator declared.
    if len(m.validators) < 1:
        errors.append("validators must declare at least one rule")

    # §14.11 item 4 — ideal_solution.md must exist (was warning; now an error).
    ideal = loaded.folder / "ideal_solution.md"
    if not ideal.exists():
        errors.append(f"ideal_solution.md not found: {ideal}")

    # P0-2 — every non-tutorial mission must also ship ``ideal_solution.diff``
    # so the post-mortem walkthrough's three-way diff has a "what was
    # expected" layer. Tutorial missions are exempt (the report layer is
    # also short-circuited for them).
    ideal_diff = loaded.folder / "ideal_solution.diff"
    if not is_tutorial:
        if not ideal_diff.exists():
            errors.append(
                f"ideal_solution.diff not found: {ideal_diff} — P0-2 requires "
                "every non-tutorial mission to ship the canonical fix diff."
            )
        else:
            diff_body = ideal_diff.read_text(encoding="utf-8", errors="replace").strip()
            if not diff_body:
                errors.append(f"ideal_solution.diff is empty: {ideal_diff}")
            elif "diff --git" not in diff_body and "--- a/" not in diff_body:
                errors.append(
                    f"ideal_solution.diff does not look like a unified diff: {ideal_diff}"
                )

    # §14.11 item 5a — expected_context.required >= 2.
    if len(m.expected_context.required) < 2:
        errors.append("expected_context.required must list >= 2 files")

    # §14.11 item 5b — expected_context.discouraged >= 1.
    # Tutorial missions don't score context selection; we still
    # recommend a discouraged entry for teaching purposes (Mission 00
    # does ship one), but we soften the check to a non-error for them.
    if not is_tutorial and len(m.expected_context.discouraged) < 1:
        errors.append("expected_context.discouraged must list >= 1 file")

    # §14.11 item 6 — reward_signals.prompt_quality.must_include_any >= 3.
    # Tutorial missions don't grade prompt_quality, so the threshold is
    # waived — but we still require at least one entry so the manifest
    # author thinks about what a good orientation prompt looks like.
    min_pq = 1 if is_tutorial else 3
    if len(m.reward_signals.prompt_quality.must_include_any) < min_pq:
        errors.append(
            f"reward_signals.prompt_quality.must_include_any must list >= {min_pq} keywords"
        )

    # §14.11 item 7 — expected_diff_lines_p50 is set and plausible.
    # The diff-minimality dimension divides the submitted diff churn by p50;
    # an out-of-band value silently shifts the scoring band across the whole
    # mission. p50=1 makes any non-trivial fix score 0; p50=1000 lets a
    # full-file rewrite score 10/10. The 3..200 band covers realistic
    # supervision-task fix sizes (single-line tweak through a small refactor).
    if m.expected_diff_lines_p50 <= 0:
        errors.append("expected_diff_lines_p50 must be > 0")
    elif m.expected_diff_lines_p50 < 3:
        errors.append(
            f"expected_diff_lines_p50={m.expected_diff_lines_p50} is implausibly small "
            "(< 3). At this value, any realistic fix scores 0/10 on diff_minimality. "
            "Set to the line count of the ideal solution diff."
        )
    elif m.expected_diff_lines_p50 > 200:
        errors.append(
            f"expected_diff_lines_p50={m.expected_diff_lines_p50} is implausibly large "
            "(> 200). At this value, even runaway rewrites score 10/10 on "
            "diff_minimality. Set to the line count of the ideal solution diff."
        )

    # §14.11 item 8 — mission id appears in catalog seed (best-effort).
    # Tutorial missions are seeded by migration 0011's disk-rescan step, not
    # by the 0003 hand-written list, so we skip this check for them.
    if not is_tutorial and seed_path is not None and seed_path.exists():
        if not _check_seed_contains(seed_path, m.id):
            errors.append(f"mission id '{m.id}' not found in catalog seed {seed_path}")

    # Verify each forbidden_changes validator points at an existing file.
    for v in m.validators:
        if getattr(v, "kind", "") == "forbidden_changes":
            rules_file = loaded.folder / v.rules_file  # type: ignore[union-attr]
            if not rules_file.exists():
                errors.append(f"forbidden_changes.rules_file not found: {rules_file}")

    # hidden_tests.command — if it references a runner script in the mission
    # folder, that script must exist (best-effort string parse).
    cmd = m.hidden_tests.command or ""
    if "hidden_tests/" in cmd:
        # Extract trailing-most filename token after hidden_tests/.
        for token in cmd.split():
            if token.startswith("hidden_tests/") or "/hidden_tests/" in token:
                ref = loaded.folder / token.split("hidden_tests/")[-1]
                ref = loaded.folder / "hidden_tests" / Path(token).name
                if not ref.exists():
                    errors.append(f"hidden_tests command references missing file: {ref}")

    # Optional acceptance.yaml — if present, must parse.
    acceptance_yaml = loaded.folder / "acceptance.yaml"
    if acceptance_yaml.exists():
        try:
            load_acceptance(acceptance_yaml)
        except Exception as exc:
            errors.append(f"acceptance.yaml failed to parse: {exc}")

    # Brief-leak linter (P1-4): the brief must not telegraph fix idioms that
    # appear verbatim in the ideal_solution.md. The mission tests supervision
    # of the AGENT, not the supervisor's ability to grep the brief for a
    # code-style token.
    errors.extend(_check_brief_leak(loaded))

    return errors


def _resolve_seed_path(root: Path) -> Path | None:
    """Locate the catalog seed migration relative to the missions root.

    The seed lives at ``apps/api/alembic/versions/0003_seed_missions.py``.
    When ``--root`` points at the canonical ``<repo>/missions`` folder, we
    can derive it; otherwise return None (and skip the seed check).
    """
    candidate = root.parent / "apps" / "api" / "alembic" / "versions" / "0003_seed_missions.py"
    return candidate if candidate.exists() else None


def validate_root(root: Path) -> int:
    loader = MissionLoader(root)
    try:
        missions = loader.scan()
    except Exception as exc:
        print(f"FAIL parse error in {root}: {exc}", file=sys.stderr)
        return 2

    if not missions:
        print(f"FAIL no missions found under {root}", file=sys.stderr)
        return 2

    seed_path = _resolve_seed_path(root)

    failures = 0
    for m in missions:
        errors = _check_one(m, seed_path)
        if errors:
            failures += 1
            print(f"FAIL [{m.manifest.id}] {len(errors)} error(s):", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
        else:
            print(f"OK   [{m.manifest.id}] v{m.manifest.version}")

    if failures:
        print(f"\n{failures} mission(s) failed validation", file=sys.stderr)
        return 2
    print(f"\nAll {len(missions)} mission(s) valid")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="missions/ directory (defaults to MISSIONS_ROOT)",
    )
    args = parser.parse_args(argv)

    if args.root is None:
        try:
            from app.config import get_settings

            args.root = get_settings().missions_root
        except Exception as exc:
            print(f"could not resolve missions root: {exc}", file=sys.stderr)
            return 1

    return validate_root(args.root)


if __name__ == "__main__":
    sys.exit(main())
