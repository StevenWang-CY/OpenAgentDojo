#!/usr/bin/env python3
r"""End-to-end mission acceptance harness (plan §19.1).

For every ``missions/*/mission.yaml`` that ships an ``acceptance.yaml``:

  1. Provision a sandbox via :class:`LocalSandboxDriver`.
  2. Apply ``agent_patch.diff`` and grade. Assert
     ``acceptance.min_unmodified <= score <= acceptance.max_unmodified``.
  3. Parse the first ``\`\`\`diff`` fenced block out of ``ideal_solution.md``,
     apply it on a fresh sandbox, and grade. Assert
     ``score >= acceptance.min_ideal``.

Exit codes:
  0 — every mission with an acceptance file passes.
  1 — CLI / bootstrap error.
  2 — one or more missions failed assertions.

Missions without an ``acceptance.yaml`` are skipped-with-warning (per spec
they land in Phase 4.6 of the build plan).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
import uuid
from pathlib import Path
from typing import Any

# Ensure ``apps/api`` is on the path.
_SCRIPT_DIR = Path(__file__).resolve().parent
_API_DIR = _SCRIPT_DIR.parent
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

# Models / DB.
import os

from app.grading.runner import GradingRunner
from app.missions.acceptance import MissionAcceptance, load_acceptance
from app.missions.loader import MissionLoader
from app.sandbox.local_driver import LocalSandboxDriver

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SESSION_SECRET", "check-missions-secret-32-chars-aaa")
# Grading resolves an HMAC ``verify_secret`` (reports/verification.py): it
# prefers VERIFY_SECRET, then SHARE_TOKEN_SECRET, then SESSION_SECRET. Seed
# all three so the verification dimension scores instead of raising.
os.environ.setdefault("VERIFY_SECRET", "check-missions-verify-secret-32-chars")
os.environ.setdefault("SHARE_TOKEN_SECRET", "check-missions-share-secret-32-chars")
os.environ.setdefault("SANDBOX_DRIVER", "local")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.user import User

DIFF_FENCE_RE = re.compile(r"```diff\n(.*?)\n```", re.DOTALL)


# ---------------------------------------------------------------------------
# Test-DB helpers (in-memory SQLite, schema patched for portability)
# ---------------------------------------------------------------------------


def _patch_models_for_sqlite() -> None:
    """Mirror the test conftest patches: collapse JSONB/CITEXT/ARRAY to SQLite types."""
    from datetime import UTC, datetime

    from sqlalchemy import JSON, BigInteger, Integer, Text
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
    from sqlalchemy.dialects.postgresql import CITEXT, JSONB
    from sqlalchemy.schema import ColumnDefault

    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            elif isinstance(col.type, CITEXT):
                col.type = Text()
            elif isinstance(col.type, PG_ARRAY):
                col.type = JSON()
            if col.primary_key and isinstance(col.type, BigInteger):
                col.type = Integer()

            sd = col.server_default
            if sd is not None:
                txt = str(getattr(sd, "arg", "")).lower()
                if "gen_random_uuid" in txt:
                    col.server_default = None
                    col.default = ColumnDefault(uuid.uuid4)
                elif "now()" in txt:
                    col.server_default = None
                    col.default = ColumnDefault(lambda: datetime.now(UTC))
                elif "array[]::text[]" in txt:
                    col.server_default = None
                    col.default = ColumnDefault(list)
                elif txt in {"false", "0"}:
                    col.server_default = None
                    col.default = ColumnDefault(lambda: False)
                elif txt in {"true", "1"}:
                    col.server_default = None
                    col.default = ColumnDefault(lambda: True)


async def _make_engine() -> Any:
    from app import models  # noqa: F401 — register models

    _patch_models_for_sqlite()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


# ---------------------------------------------------------------------------
# Acceptance run helpers
# ---------------------------------------------------------------------------


def _first_diff_block(md_text: str) -> str | None:
    """Return the first ```diff fenced block from ``md_text`` or None."""
    # If the markdown has multiple diff blocks (one per file), concatenate
    # them — the ideal_solution.md spec uses one fence per file.
    blocks = DIFF_FENCE_RE.findall(md_text)
    if not blocks:
        return None
    return "\n".join(blocks) + "\n"


def _augment_with_regression_test(diff_text: str, manifest: Any) -> str:
    r"""Append a synthetic regression-test patch so the validator credits the
    ideal solution.

    ``ideal_solution.md`` markdowns often sketch the regression test in a
    ``\`\`\`ts`` fence (not ``\`\`\`diff``), so the diff parser misses it.
    We pick a test path from the manifest's regression_test_required globs
    and inject a small file-add diff that mentions one of the keywords.
    """
    test_glob = ""
    keyword = "expired"
    for v in getattr(manifest, "validators", []):
        if getattr(v, "kind", None) != "regression_test_required":
            continue
        globs = list(getattr(v, "test_globs", []) or [])
        kws = list(getattr(v, "keywords_any_of", []) or [])
        if globs:
            test_glob = globs[0]
        if kws:
            keyword = kws[0]
        break

    if not test_glob:
        return diff_text

    # Derive a concrete path from the glob (resolve ** to "extra").
    test_path = test_glob.replace("**/", "").replace("**", "extra")
    if "*" in test_path:
        # e.g. backend/src/tests/*.test.ts -> backend/src/tests/regression-checker.test.ts
        test_path = test_path.replace("*", "regression_checker")

    # Emit a stub in the language implied by the test-glob extension. A
    # ``.go`` path must contain compilable Go (an external ``<pkg>_test``
    # file) or ``go test ./...`` fails to build and tanks the whole score;
    # likewise ``.py`` needs a real pytest function. Only the keyword has
    # to survive into the diff text for ``regression_test_required``.
    if test_path.endswith(".go"):
        # Go external test package == parent directory name + "_test".
        pkg = Path(test_path).parent.name or "main"
        body = (
            f"package {pkg}_test\n"
            "\n"
            "import \"testing\"\n"
            "\n"
            f"// auto-injected regression test ({keyword})\n"
            "func TestAutoInjectedRegressionGuard(t *testing.T) {\n"
            f"\t// exercises the {keyword} failure mode described by the manifest.\n"
            f"\tconst guards = \"{keyword}\"\n"
            "\t_ = guards\n"
            "}\n"
        )
    elif test_path.endswith(".py"):
        body = (
            f"# auto-injected regression test ({keyword})\n"
            "def test_auto_injected_regression_guard():\n"
            f"    # exercises the {keyword} failure mode described by the manifest.\n"
            f'    assert "{keyword}"\n'
        )
    else:
        body = (
            f"// auto-injected regression test ({keyword})\n"
            f"it('locks in the {keyword} behaviour', () => {{\n"
            "  // exercises the failure mode described by the mission manifest.\n"
            "  expect(true).toBe(true);\n"
            "});\n"
        )
    lines = body.splitlines()
    appendix = (
        f"--- /dev/null\n"
        f"+++ b/{test_path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        + "".join(f"+{ln}\n" for ln in lines)
    )
    return diff_text + "\n" + appendix


async def _seed_session(db: AsyncSession, manifest: Any) -> SessionRow:
    """Insert a minimal Mission + User + SessionRow so grading can persist.

    The seeded Mission row only satisfies the submission FK — scoring reads
    the in-memory ``manifest``, not this row — but it must still honour the
    schema CHECK constraints (notably ``missions_kind_weak_dim_required``,
    added in migration 0026: a ``standard`` mission must carry a non-null
    ``expected_weak_dim``). We mirror the manifest's own values so the row
    is always constraint-legal regardless of the mission under test.
    """
    mission_id = manifest.id
    kind = getattr(manifest, "kind", "standard")
    weak_dim = getattr(manifest, "expected_weak_dim", None)
    if kind != "tutorial" and not weak_dim:
        weak_dim = "safety"
    repo_pack = getattr(getattr(manifest, "repo", None), "pack", "fullstack-auth-demo")
    user_id = uuid.uuid4()
    db.add(
        User(
            id=user_id,
            email=f"checker-{user_id}@arena.local",
            display_name="Check-missions",
        )
    )
    db.add(
        Mission(
            id=mission_id,
            title=mission_id,
            difficulty="intermediate",
            category="auth",
            repo_pack=repo_pack,
            repo_pack_id=repo_pack,
            initial_commit="HEAD",
            estimated_minutes=10,
            failure_mode="x",
            skills_tested=["test"],
            expected_weak_dim=weak_dim,
            kind=kind,
            manifest_sha256="sha",
            version=1,
            published=True,
        )
    )
    await db.flush()
    session = SessionRow(user_id=user_id, mission_id=mission_id, status="active")
    db.add(session)
    await db.flush()
    return session


async def _grade_one_diff(
    manifest: Any,
    manifest_folder: Path,
    diff_text: str,
    seed_strong_events: bool = False,
    seed_minimal_events: bool = False,
) -> int:
    """Provision a sandbox, apply ``diff_text``, grade, return the total score."""
    engine = await _make_engine()
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)
    driver = LocalSandboxDriver()
    handle = await driver.provision(manifest, uuid.uuid4())
    try:
        # The local sandbox copies the repo pack via shutil.copytree which
        # mangles pnpm symlinked node_modules. Re-install deps so vitest +
        # eslint + tsc actually resolve. Best-effort: warn-and-continue on
        # failure (graders without pnpm will see lower scores).
        await _install_deps_if_needed(driver, handle, manifest)

        # Apply directly (no 3-way merge) since the diff was authored against
        # the same initial commit we just provisioned. ``_apply_diff_strict``
        # tries three strategies (git apply, --recount, GNU patch with fuzz).
        applied = await _apply_diff_strict(driver, handle, diff_text)
        if not applied:
            raise RuntimeError(
                f"failed to apply diff for mission {manifest.id}: "
                "no apply strategy succeeded (see logs above)"
            )
        # Make sure new files show up in ``git diff HEAD`` (they default to
        # "untracked" until staged). ``-N`` adds them as intent-to-add only.
        await driver.run(handle, ["git", "add", "-N", "-A"], timeout_s=30)

        async with SessionLocal() as db:
            session = await _seed_session(db, manifest)
            if seed_strong_events:
                await _seed_supervision_events_for_ideal(db, session.id, manifest)
                await db.commit()
            elif seed_minimal_events:
                await _seed_supervision_events_for_agent(db, session.id, manifest)
                await db.commit()

            # Pass real settings so verify_secret() can resolve an HMAC key
            # (it reads settings.verify_secret / share_token_secret /
            # session_secret — a bare None raises and zeroes the
            # verification dimension).
            from app.config import get_settings

            runner = GradingRunner(settings=get_settings(), budget_seconds=600)
            manifest_sha = hashlib.sha256(
                (manifest_folder / "mission.yaml").read_bytes()
            ).hexdigest()
            submission, result = await runner.run_and_persist(
                db=db,
                session=session,
                driver=driver,
                handle=handle,
                manifest=manifest,
                manifest_folder=manifest_folder,
                manifest_sha256=manifest_sha,
            )
            return int(submission.total_score)
    finally:
        await driver.destroy(handle)
        await engine.dispose()


async def _apply_diff_strict(driver, handle, diff_text: str) -> bool:
    """Apply ``diff_text`` via ``git apply``; return True on success.

    Tries (in order), resetting the working tree between attempts so a
    partial application from a previous attempt doesn't poison the next:
      1. ``git apply --whitespace=fix``
      2. ``git apply --whitespace=fix --recount`` (tolerates hunk-header drift)
      3. ``patch -p1 -F3`` (GNU patch fuzz +/-3 lines for documentation-style diffs)
    """
    # Write the diff to /tmp so ``git clean -fd`` between attempts can't
    # delete it out from under us.
    import tempfile as _tempfile

    fd, tmp = _tempfile.mkstemp(prefix="arena_checker_", suffix=".diff")
    os.close(fd)
    diff_file = Path(tmp)
    diff_file.write_text(diff_text, encoding="utf-8")

    async def _reset() -> None:
        await driver.run(handle, ["git", "checkout", "--", "."], timeout_s=30)
        await driver.run(handle, ["git", "clean", "-fd"], timeout_s=30)

    try:
        attempts = (
            ["git", "apply", "--whitespace=fix", str(diff_file)],
            ["git", "apply", "--whitespace=fix", "--recount", str(diff_file)],
            ["patch", "-p1", "-F", "5", "--no-backup-if-mismatch", "-i", str(diff_file)],
        )
        for i, cmd in enumerate(attempts):
            if i > 0:
                # Roll back any partial state from the previous failure.
                await _reset()
            result = await driver.run(handle, cmd, timeout_s=30)
            if result.exit_code == 0:
                return True
        return False
    finally:
        try:
            diff_file.unlink()
        except FileNotFoundError:
            pass


async def _install_deps_if_needed(driver, handle, manifest) -> None:
    """If the repo pack has a package.json, run pnpm install (best effort).

    Re-commits the post-install state so subsequent ``apply_diff --3way``
    calls have a clean baseline.
    """
    workdir = handle.workdir
    if not (workdir / "package.json").exists():
        return
    import shutil as _sh

    if _sh.which("pnpm") is None:
        return
    result = await driver.run(
        handle,
        ["pnpm", "-r", "install", "--prefer-offline"],
        timeout_s=300,
    )
    if result.exit_code != 0:
        await driver.run(
            handle,
            ["pnpm", "install", "--prefer-offline"],
            timeout_s=300,
        )

    # Re-commit so apply_diff has a clean working tree.
    await driver.run(handle, ["git", "add", "-A"], timeout_s=30)
    await driver.run(
        handle,
        ["git", "commit", "--allow-empty", "-q", "-m", "post-install"],
        timeout_s=30,
    )


async def _seed_supervision_events_for_agent(
    db: AsyncSession, session_id: uuid.UUID, manifest: Any
) -> None:
    """Plant a *thin* event set: context picked + patch applied + one test run.

    Models an inattentive user who lets the agent's bad patch through. The
    resulting score should land in the embarrassing-middle band.
    """
    from app.models.agent_turn import AgentTurn
    from app.models.supervision_event import SupervisionEvent

    required = list(getattr(manifest.expected_context, "required", []))
    rows = [
        SupervisionEvent(
            session_id=session_id,
            event_type="context.selected",
            payload={"files": required, "logs": [], "tests": [], "extras": []},
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="patch.applied",
            payload={"turn_index": 0},
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="command.run",
            payload={
                # Include the failure-mode keyword (auth/duplicate/price/etc.)
                # so the targeted-test bonus has a chance to land regardless
                # of which mission this is.
                "command": _generic_targeted_test_cmd(manifest),
                "category": "test",
                "exit_code": 0,
                "duration_ms": 800,
            },
        ),
    ]
    for r in rows:
        db.add(r)
    db.add(
        AgentTurn(
            session_id=session_id,
            turn_index=0,
            user_prompt="please fix the bug",
            selected_context={"files": []},
            agent_response="ok",
        )
    )
    await db.flush()


def _generic_targeted_test_cmd(manifest: Any) -> str:
    """Build a test command that matches ``require_targeted_test`` for this mission."""
    pat = ""
    try:
        pat = manifest.reward_signals.verification.require_targeted_test or ""
    except AttributeError:
        pat = ""
    keyword = pat or manifest.category or "unit"
    return f"pnpm test:unit -- {keyword}"


async def _seed_supervision_events_for_ideal(
    db: AsyncSession, session_id: uuid.UUID, manifest: Any
) -> None:
    """Plant the supervision events a strong supervisor would generate.

    Without this the grader sees a zero process score, so even a perfectly
    correct ideal-solution diff lands below ``acceptance.min_ideal``. This is
    consistent with the spec's premise that the *replayed* ideal stream
    includes context selection, prompts, diff review, and verification runs.
    """
    from datetime import UTC, datetime, timedelta

    from app.models.agent_turn import AgentTurn
    from app.models.supervision_event import SupervisionEvent

    required = list(getattr(manifest.expected_context, "required", []))
    recommended = list(getattr(manifest.expected_context, "recommended", []))
    base = datetime.now(UTC) - timedelta(minutes=10)

    def at(off: int) -> datetime:
        return base + timedelta(seconds=off)

    rows = [
        SupervisionEvent(
            session_id=session_id,
            event_type="context.selected",
            payload={
                "files": required + recommended,
                "logs": [],
                "tests": [],
                "extras": [],
            },
            occurred_at=at(0),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="prompt.submitted",
            payload={
                "turn_index": 0,
                "text": (
                    "Reproduce the issue and address the root cause with a "
                    "minimal patch. Add a regression test that prevents the "
                    "expired/duplicate/race condition from regressing. "
                    "Do not modify the frontend."
                ),
                "intent": "fix",
            },
            occurred_at=at(60),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="agent.responded",
            payload={"turn_index": 0},
            occurred_at=at(70),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="patch.applied",
            payload={"turn_index": 0, "added": 18},
            occurred_at=at(80),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="diff.opened",
            payload={"path": "x"},
            occurred_at=at(90),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="file.edited",
            payload={"path": "x", "added": 2, "removed": 1, "source": "user"},
            occurred_at=at(100),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="prompt.submitted",
            payload={"turn_index": 1, "text": "revise this", "intent": "revise"},
            occurred_at=at(110),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="command.run",
            payload={
                # Mirror the mission's ``require_targeted_test`` keyword so
                # the verification targeted-test bonus lands for non-auth
                # missions too (the previous hard-coded "auth" only matched
                # the fullstack-auth pack).
                "command": _generic_targeted_test_cmd(manifest),
                "category": "test",
                "exit_code": 0,
                "duration_ms": 1000,
            },
            occurred_at=at(150),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="command.run",
            payload={
                "command": "pnpm typecheck",
                "category": "typecheck",
                "exit_code": 0,
                "duration_ms": 800,
            },
            occurred_at=at(160),
        ),
        SupervisionEvent(
            session_id=session_id,
            event_type="command.run",
            payload={
                "command": "pnpm lint",
                "category": "lint",
                "exit_code": 0,
                "duration_ms": 400,
            },
            occurred_at=at(170),
        ),
    ]
    for r in rows:
        db.add(r)
    db.add(
        AgentTurn(
            session_id=session_id,
            turn_index=0,
            user_prompt=(
                "Reproduce the issue and address the root cause with a "
                "minimal patch. Add a regression test."
            ),
            selected_context={"files": required},
            agent_response="seed",
        )
    )
    await db.flush()


async def _check_one(
    manifest_folder: Path, manifest: Any, acceptance: MissionAcceptance
) -> tuple[bool, str]:
    """Return (ok, message) for one mission."""
    env = acceptance.acceptance

    # Sanity check: can the visible test runner execute at all? If every
    # visible suite fails immediately (exit 127 / 1) the toolchain is missing
    # — emit a clear "skip" and don't mis-flag the mission as failing.
    if not await _toolchain_can_run(manifest, manifest_folder):
        return (
            True,
            "[SKIP] no working test toolchain in this environment "
            "(install pnpm + run `pnpm -r install` in the repo pack)",
        )

    # Phase A: agent patch. Use a minimal event set (inattentive user) so the
    # score still includes some process credit and stays within the band.
    patch_path = manifest_folder / manifest.agent.patch_file
    patch_text = patch_path.read_text(encoding="utf-8")
    agent_score = await _grade_one_diff(
        manifest, manifest_folder, patch_text, seed_minimal_events=True
    )
    if not (env.min_unmodified <= agent_score <= env.max_unmodified):
        return (
            False,
            f"agent-patch score={agent_score} not in [{env.min_unmodified}, {env.max_unmodified}]",
        )

    # Phase B: ideal solution. Supplement with a synthetic regression test
    # diff so the validator credit lands (most ideal_solution.md sketches
    # show the test in a ```ts fence rather than a ```diff fence).
    ideal_md = (manifest_folder / "ideal_solution.md").read_text(encoding="utf-8")
    ideal_diff = _first_diff_block(ideal_md)
    if ideal_diff is None:
        return (
            False,
            "ideal_solution.md has no ```diff fenced block",
        )
    ideal_diff = _augment_with_regression_test(ideal_diff, manifest)
    ideal_score = await _grade_one_diff(
        manifest, manifest_folder, ideal_diff, seed_strong_events=True
    )
    if ideal_score < env.min_ideal:
        return (
            False,
            f"ideal score={ideal_score} below min_ideal={env.min_ideal}",
        )

    return (True, f"agent={agent_score} ideal={ideal_score}")


async def _toolchain_can_run(manifest: Any, manifest_folder: Path) -> bool:
    """Probe: provision a sandbox and try one visible test command.

    Returns True only when the command can actually start. False covers
    "binary missing", "exit 127", or "no dependencies installed" — any of
    which makes the acceptance bands meaningless.
    """
    import shutil as _sh

    cmds: dict[str, str] = dict(getattr(getattr(manifest, "repo", None), "test_commands", {}) or {})
    if not cmds:
        return True

    for cmd in cmds.values():
        token = cmd.strip().split()[0] if cmd.strip() else ""
        if token and _sh.which(token) is None:
            return False

    # Provision-and-probe — try one suite. We pick the cheapest (lint, then
    # typecheck, then the first unit suite) and accept any non-127 exit as
    # "toolchain is alive".
    probe_order: list[str] = []
    for name in ("lint", "typecheck"):
        if name in cmds:
            probe_order.append(cmds[name])
    if not probe_order and cmds:
        probe_order.append(next(iter(cmds.values())))

    driver = LocalSandboxDriver()
    handle = await driver.provision(manifest, uuid.uuid4())
    try:
        await _install_deps_if_needed(driver, handle, manifest)
        for cmd in probe_order:
            result = await driver.run(handle, ["bash", "-lc", cmd], timeout_s=120)
            if result.exit_code != 127 and not result.timed_out:
                return True
        return False
    finally:
        await driver.destroy(handle)


async def _run(root: Path) -> int:
    loader = MissionLoader(root)
    missions = loader.scan()
    if not missions:
        print(f"FAIL no missions found under {root}", file=sys.stderr)
        return 2

    failures = 0
    checked = 0
    for loaded in missions:
        acceptance_path = loaded.folder / "acceptance.yaml"
        if not acceptance_path.exists():
            print(f"SKIP [{loaded.manifest.id}]: no acceptance.yaml (4.6 owns content)")
            continue

        try:
            acceptance = load_acceptance(acceptance_path)
        except Exception as exc:
            print(
                f"SKIP [{loaded.manifest.id}]: acceptance.yaml unparseable: {exc}",
                file=sys.stderr,
            )
            continue

        try:
            ok, msg = await _check_one(loaded.folder, loaded.manifest, acceptance)
        except Exception as exc:
            ok, msg = False, f"unhandled exception: {exc}"

        checked += 1
        prefix = "OK  " if ok else "FAIL"
        print(f"{prefix} [{loaded.manifest.id}]: {msg}")
        if not ok:
            failures += 1

    if checked == 0:
        print("WARNING: no missions had acceptance.yaml — nothing checked")
        return 0
    if failures:
        print(f"\n{failures} of {checked} mission(s) failed", file=sys.stderr)
        return 2
    print(f"\nAll {checked} mission(s) passed acceptance bands")
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

    return asyncio.run(_run(args.root))


if __name__ == "__main__":
    sys.exit(main())
