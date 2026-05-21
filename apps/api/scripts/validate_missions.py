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

from app.missions.acceptance import load_acceptance
from app.missions.loader import LoadedMission, MissionLoader


def _check_seed_contains(seed_path: Path, mission_id: str) -> bool:
    """Return True if ``mission_id`` appears as a string literal in the seed."""
    if not seed_path.exists():
        return False
    text = seed_path.read_text(encoding="utf-8")
    needles = (f'"{mission_id}"', f"'{mission_id}'")
    return any(n in text for n in needles)


def _check_one(loaded: LoadedMission, seed_path: Path | None) -> list[str]:  # noqa: PLR0912 — §14.11 checklist is intentionally exhaustive
    """Return a list of human-readable error strings for one mission."""
    m = loaded.manifest
    errors: list[str] = []

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

    # §14.11 item 5a — expected_context.required >= 2.
    if len(m.expected_context.required) < 2:
        errors.append("expected_context.required must list >= 2 files")

    # §14.11 item 5b — expected_context.discouraged >= 1.
    if len(m.expected_context.discouraged) < 1:
        errors.append("expected_context.discouraged must list >= 1 file")

    # §14.11 item 6 — reward_signals.prompt_quality.must_include_any >= 3.
    if len(m.reward_signals.prompt_quality.must_include_any) < 3:
        errors.append("reward_signals.prompt_quality.must_include_any must list >= 3 keywords")

    # §14.11 item 7 — expected_diff_lines_p50 is set (>0). The manifest's
    # default is 20; treat 0 / negative as unset.
    if m.expected_diff_lines_p50 <= 0:
        errors.append("expected_diff_lines_p50 must be > 0")

    # §14.11 item 8 — mission id appears in catalog seed (best-effort).
    if seed_path is not None and seed_path.exists():
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
