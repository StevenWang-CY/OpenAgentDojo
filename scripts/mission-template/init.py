#!/usr/bin/env python3
"""Mission-authoring scaffolder (P1-1 contributor accelerator).

Run from the repo root:

    python scripts/mission-template/init.py

Interactive prompts collect the closed-vocabulary metadata the manifest
model demands (mission id, repo pack, failure-mode tag, difficulty,
category, estimated minutes) then materialise a new
``missions/<NN>-<id>/`` directory by stamping the templates under
``scripts/mission-template/template/``.

The script is intentionally NOT bundled into the production image — it's
a contributor accelerator that lives at the repo root and is invoked at
authoring time. See ``scripts/mission-template/README.md`` for the full
author workflow.

Design notes
------------
* The closed-vocabulary tags mirror
  ``apps/api/app/missions/manifest.py::_FAILURE_MODE_TAGS``. We don't
  import it because the script must run from a checkout without
  installing the API (a contributor on the docs side shouldn't have to
  ``uv sync``).
* Existing missions on disk are scanned to pick the next ``NN`` prefix;
  the script refuses to overwrite an existing directory.
* Stdin-driven so the script tests cleanly: feed answers on stdin and
  assert against the materialised file tree.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Callable, Iterable

# Vocabularies — kept in lockstep with apps/api/app/missions/manifest.py.
_FAILURE_MODE_TAGS: tuple[str, ...] = (
    "checks_presence_not_expiration",
    "overfitted_visible_test",
    "wrong_layer_committed",
    "missing_regression_test",
    "race_condition",
    "context_dropped",
    "error_wrapped_swallowed",
    "dependency_misuse",
    "security_check_removed",
    "typecheck_ignored",
    "api_contract_drift",
    "excessive_rewrite",
    "goroutine_leak",
)

_REPO_PACKS: dict[str, tuple[str, str]] = {
    # pack id -> (language, language_runtime)
    "fullstack-auth-demo": ("typescript", "node20"),
    "data-api-demo": ("python", "python312"),
    "go-orders-service": ("go", "go122"),
}

_DIFFICULTIES: tuple[str, ...] = ("beginner", "intermediate", "advanced")

# Categories from the shipped manifests — `category` is a free-form string
# but we surface the established set so authors don't fragment it casually.
_RECOMMENDED_CATEGORIES: tuple[str, ...] = (
    "api",
    "auth",
    "database",
    "debugging",
    "refactoring",
    "security",
    "testing",
)

_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_MISSION_DIR_RE = re.compile(r"^(\d{2})-(.+)$")


@dataclass
class MissionInputs:
    mission_id: str
    repo_pack: str
    failure_mode: str
    difficulty: str
    category: str
    estimated_minutes: int

    @property
    def language(self) -> str:
        return _REPO_PACKS[self.repo_pack][0]

    @property
    def language_runtime(self) -> str:
        return _REPO_PACKS[self.repo_pack][1]

    @property
    def lang_tag(self) -> str:
        return f"lang:{self.language}"


def _repo_root() -> Path:
    """Return the repo root by climbing from this script.

    Layout: ``scripts/mission-template/init.py`` → climb two parents.
    """
    return Path(__file__).resolve().parents[2]


def _missions_dir(root: Path | None = None) -> Path:
    return (root or _repo_root()) / "missions"


def _next_index(missions_dir: Path) -> int:
    """Return the next ``NN`` prefix free for use under ``missions/``."""
    if not missions_dir.exists():
        return 1
    max_idx = -1
    for child in missions_dir.iterdir():
        if not child.is_dir():
            continue
        match = _MISSION_DIR_RE.match(child.name)
        if not match:
            continue
        idx = int(match.group(1))
        if idx > max_idx:
            max_idx = idx
    return max_idx + 1


def _prompt(
    label: str,
    *,
    choices: Iterable[str] | None = None,
    default: str | None = None,
    validate: "callable | None" = None,
    stream_in=None,
    stream_out=None,
) -> str:
    """Read one answer from stdin, optionally re-prompting on validation failure.

    ``choices`` enumerates closed-vocabulary options; we render them as a
    numbered list and accept either the number or the value. ``validate``
    is called on the resolved value and may raise ``ValueError`` to force
    a re-prompt.
    """
    stream_in = stream_in or sys.stdin
    stream_out = stream_out or sys.stdout

    suffix = f" [{default}]" if default else ""
    options_block = ""
    if choices is not None:
        opts = list(choices)
        options_block = "\n".join(f"  {i + 1}) {o}" for i, o in enumerate(opts))
        options_block += "\n"

    while True:
        if options_block:
            stream_out.write(options_block)
        stream_out.write(f"{label}{suffix}: ")
        stream_out.flush()
        raw = stream_in.readline()
        if not raw:
            # EOF on stdin — fall back to default if available, else fail.
            if default is not None:
                return default
            raise SystemExit("[init] stdin closed before all prompts were answered")
        answer = raw.strip()
        if not answer and default is not None:
            answer = default
        if choices is not None:
            opts = list(choices)
            # Accept number-keyed selection.
            if answer.isdigit():
                idx = int(answer) - 1
                if 0 <= idx < len(opts):
                    answer = opts[idx]
                else:
                    stream_out.write(
                        f"[init] choice {idx + 1} is out of range; pick 1..{len(opts)}\n"
                    )
                    continue
            if answer not in opts:
                stream_out.write(
                    f"[init] {answer!r} is not in the closed vocabulary; "
                    f"valid options: {opts!r}\n"
                )
                continue
        if validate is not None:
            try:
                validate(answer)
            except ValueError as exc:
                stream_out.write(f"[init] {exc}\n")
                continue
        return answer


def _validate_id(value: str) -> None:
    if not _ID_RE.match(value):
        raise ValueError(
            f"mission id {value!r} must match ^[a-z][a-z0-9-]*$ "
            "(kebab-case, lowercase, no leading digit)"
        )


def _validate_minutes(value: str) -> None:
    try:
        n = int(value)
    except ValueError as exc:
        raise ValueError(f"estimated_minutes {value!r} is not an integer") from exc
    if not (5 <= n <= 120):
        raise ValueError(
            f"estimated_minutes {n} is outside the supported 5-120 range"
        )


def collect_inputs(stream_in=None, stream_out=None) -> MissionInputs:
    mission_id = _prompt(
        "mission id (kebab-case)",
        validate=_validate_id,
        stream_in=stream_in,
        stream_out=stream_out,
    )
    repo_pack = _prompt(
        "repo pack",
        choices=_REPO_PACKS.keys(),
        stream_in=stream_in,
        stream_out=stream_out,
    )
    failure_mode = _prompt(
        "failure mode (closed vocabulary)",
        choices=_FAILURE_MODE_TAGS,
        stream_in=stream_in,
        stream_out=stream_out,
    )
    difficulty = _prompt(
        "difficulty",
        choices=_DIFFICULTIES,
        default="intermediate",
        stream_in=stream_in,
        stream_out=stream_out,
    )
    category = _prompt(
        "category (recommended values shown; free-form allowed)",
        choices=_RECOMMENDED_CATEGORIES,
        default="debugging",
        stream_in=stream_in,
        stream_out=stream_out,
    )
    minutes_raw = _prompt(
        "estimated_minutes (5-120)",
        default="30",
        validate=_validate_minutes,
        stream_in=stream_in,
        stream_out=stream_out,
    )
    return MissionInputs(
        mission_id=mission_id,
        repo_pack=repo_pack,
        failure_mode=failure_mode,
        difficulty=difficulty,
        category=category,
        estimated_minutes=int(minutes_raw),
    )


def _template_dir() -> Path:
    return Path(__file__).resolve().parent / "template"


_TEMPLATE_FILES: tuple[str, ...] = (
    "mission.yaml",
    "README.md",
    "agent_patch.diff",
    "forbidden_changes.yaml",
    "acceptance.yaml",
    "ideal_solution.md",
    "hidden_tests/runner.sh",
    "prompts/response.md",
    "prompts/reasoning.md",
    "prompts/intents.yaml",
)


def _render(template: str, inputs: MissionInputs) -> str:
    return Template(template).safe_substitute(
        mission_id=inputs.mission_id,
        repo_pack=inputs.repo_pack,
        failure_mode=inputs.failure_mode,
        difficulty=inputs.difficulty,
        category=inputs.category,
        estimated_minutes=str(inputs.estimated_minutes),
        language=inputs.language,
        language_runtime=inputs.language_runtime,
        lang_tag=inputs.lang_tag,
    )


def scaffold(
    inputs: MissionInputs,
    *,
    missions_root: Path | None = None,
    template_root: Path | None = None,
) -> Path:
    """Materialise the new mission directory and return its path."""
    missions_root = missions_root or _missions_dir()
    template_root = template_root or _template_dir()
    missions_root.mkdir(parents=True, exist_ok=True)

    idx = _next_index(missions_root)
    dir_name = f"{idx:02d}-{inputs.mission_id}"
    target = missions_root / dir_name
    if target.exists():
        raise FileExistsError(
            f"mission directory {target} already exists — refusing to overwrite"
        )
    target.mkdir(parents=True)

    for rel in _TEMPLATE_FILES:
        src = template_root / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not src.exists():
            raise FileNotFoundError(
                f"missing template {src} — keep ``scripts/mission-template/template`` "
                "in sync with ``_TEMPLATE_FILES``."
            )
        rendered = _render(src.read_text(encoding="utf-8"), inputs)
        dst.write_text(rendered, encoding="utf-8")
        # Preserve executable bit for hidden_tests/runner.sh.
        if src.stat().st_mode & 0o111:
            dst.chmod(dst.stat().st_mode | 0o111)
    return target


def _invoke_llm_draft(
    *,
    mission_dir: Path,
    repo_pack_id: str,
    failure_mode_title: str,
    seed_outline_file: Path,
) -> int:
    """Shell out to ``python -m app.llm.cli mission-authoring-draft``.

    The init.py script intentionally does NOT import ``app.llm.cli``
    directly — a contributor on the docs side often runs this scaffolder
    from a checkout without the API venv installed, and a bare
    ``import app.llm.cli`` would raise a noisy ``ModuleNotFoundError``.
    Shelling out lets us route around that: we add ``apps/api`` to
    ``PYTHONPATH`` so the import resolves when the API package IS on
    disk (the common dev path), and the subprocess's stderr surfaces the
    operator-actionable instruction otherwise.

    Returns the subprocess exit code (0 on success, non-zero on any
    failure). The init.py main path swallows the non-zero exit and
    prints a clean instruction rather than crashing — the mission
    skeleton has already been scaffolded; the LLM draft is opt-in
    augmentation.
    """
    repo_root = _repo_root()
    api_dir = repo_root / "apps" / "api"
    cmd = [
        sys.executable,
        "-m",
        "app.llm.cli",
        "mission-authoring-draft",
        "--mission-dir",
        str(mission_dir),
        "--repo-pack-id",
        repo_pack_id,
        "--failure-mode-title",
        failure_mode_title,
        "--seed-outline-file",
        str(seed_outline_file),
    ]
    # Build PYTHONPATH so the ``app`` package resolves. We DO NOT mutate
    # the parent ``os.environ`` in-place — credentials and other env
    # vars must continue to flow to the subprocess unchanged.
    import os

    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{api_dir}{os.pathsep}{existing_pp}" if existing_pp else str(api_dir)
    )
    try:
        result = subprocess.run(  # noqa: S603 — sys.executable + fixed argv
            cmd,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        # ``sys.executable`` should always exist; this is paranoia.
        print(
            "[init] could not invoke python; run this manually:\n"
            f"  python -m app.llm.cli mission-authoring-draft "
            f"--mission-dir {mission_dir} --repo-pack-id {repo_pack_id} "
            f"--failure-mode-title {failure_mode_title!r} "
            f"--seed-outline-file {seed_outline_file}",
            file=sys.stderr,
        )
        return 127
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mission-template-init",
        description="Scaffold a new mission directory from interactive prompts.",
    )
    parser.add_argument(
        "--missions-dir",
        type=Path,
        default=None,
        help="Override the missions root (defaults to <repo>/missions).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect inputs but don't write anything to disk.",
    )
    # P1-1 — LLM-assisted draft flags. Off by default; opt-in so a
    # contributor without LLM credentials still gets the deterministic
    # skeleton. The seed outline lives in a file rather than on the
    # CLI so authors can hand-craft a long-form brief without quoting.
    parser.add_argument(
        "--with-llm-draft",
        action="store_true",
        help=(
            "After scaffolding, also invoke the mission-authoring-draft "
            "LLM CLI to seed ``_draft/`` with model-generated artefacts. "
            "Requires --llm-failure-mode-title and --llm-seed-outline-file."
        ),
    )
    parser.add_argument(
        "--llm-failure-mode-title",
        default=None,
        help=(
            "Human-readable failure-mode title for the LLM draft (e.g. "
            "'Race condition between login and profile fetch')."
        ),
    )
    parser.add_argument(
        "--llm-seed-outline-file",
        type=Path,
        default=None,
        help="Path to a UTF-8 file containing the seed outline for the LLM draft.",
    )
    args = parser.parse_args(argv)

    if args.with_llm_draft:
        missing: list[str] = []
        if not args.llm_failure_mode_title:
            missing.append("--llm-failure-mode-title")
        if not args.llm_seed_outline_file:
            missing.append("--llm-seed-outline-file")
        if missing:
            parser.error(
                "--with-llm-draft requires: " + ", ".join(missing)
            )

    inputs = collect_inputs()
    if args.dry_run:
        print(f"[init] dry-run — would scaffold mission {inputs.mission_id}")
        return 0

    target = scaffold(inputs, missions_root=args.missions_dir)
    try:
        display = target.relative_to(_repo_root())
    except ValueError:
        display = target
    print(f"[init] scaffolded {display}")

    if args.with_llm_draft:
        assert args.llm_seed_outline_file is not None  # narrowed by parser.error above
        print(
            "[init] invoking LLM mission-authoring-draft "
            f"(repo_pack={inputs.repo_pack}, model=claude-opus-4-7)"
        )
        rc = _invoke_llm_draft(
            mission_dir=target,
            repo_pack_id=inputs.repo_pack,
            failure_mode_title=args.llm_failure_mode_title or "",
            seed_outline_file=args.llm_seed_outline_file,
        )
        if rc != 0:
            print(
                "[init] LLM draft step exited non-zero — the deterministic "
                "skeleton is intact at the path above. To regenerate after "
                "fixing credentials, run:\n"
                f"  cd apps/api && python -m app.llm.cli "
                "mission-authoring-draft "
                f"--mission-dir {target} "
                f"--repo-pack-id {inputs.repo_pack} "
                f"--failure-mode-title {args.llm_failure_mode_title!r} "
                f"--seed-outline-file {args.llm_seed_outline_file}",
                file=sys.stderr,
            )
        else:
            print(
                "[init] LLM draft written to "
                f"{target / '_draft'} — hand-promote each artefact into the "
                "canonical mission layout, then delete ``_draft/`` before "
                "the mission can load (the catalog loader refuses any "
                "folder that still carries ``_draft/``)."
            )

    print("[init] next steps:")
    print("  1. Fill in mission.yaml (brief, validators, scoring weights).")
    print("  2. Write the agent_patch.diff that produces the failure mode.")
    print("  3. Add hidden tests under hidden_tests/.")
    print("  4. Author missions/_calibration/<id>.yaml so the grader has a baseline.")
    print("  5. Bump apps/api/alembic/versions/0003_seed_missions if the seed list changes.")
    if args.with_llm_draft:
        print("  6. Hand-promote ``_draft/`` artefacts and delete the directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
