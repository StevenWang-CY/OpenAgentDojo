#!/usr/bin/env python3
"""Refresh ``repo_packs.repo_sha`` from the on-disk repo packs (P1).

For every subdirectory of ``missions/_shared/repos/`` this script:

  1. resolves the repo's HEAD SHA via ``git rev-parse HEAD``,
  2. compares it against the persisted ``repo_packs.repo_sha`` row,
  3. UPDATEs the row when the SHA drifted, and
  4. logs the (id, before, after) tuple to stdout.

Designed to run as a non-blocking step in the API entrypoint just
BEFORE ``alembic upgrade head`` so a fresh deploy lands real SHAs
instead of the placeholder zeros some historical seeds wrote. Exits
non-zero if the database is unreachable so the entrypoint can fail loud
when the misconfiguration is structural, but exits 0 on benign drift so
a transient deploy doesn't block the rolling restart.

Usage (CI / entrypoint)::

    python scripts/refresh_repo_pack_shas.py

Environment:
    DATABASE_URL must be set (or the script defaults to the same
    sqlalchemy URL the API reads via ``app.config.get_settings``).

Idempotent — safe to run on every boot.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPOS_ROOT = REPO_ROOT / "missions" / "_shared" / "repos"


def _git_head_sha(pack_dir: Path) -> str | None:
    """Return ``git rev-parse HEAD`` for ``pack_dir`` or ``None`` on miss.

    Returns ``None`` when the directory is not a git repo, when the
    binary is missing, or when the call exits non-zero — the upstream
    caller logs the skip and moves on so a single corrupt pack does not
    fail the whole refresh.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(pack_dir),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[refresh-repo-pack] {pack_dir.name}: git failed ({exc})")
        return None
    if result.returncode != 0:
        # Some packs ship pre-populated, NOT git-init'd. That's fine —
        # leave the persisted row alone.
        return None
    sha = result.stdout.strip()
    if len(sha) < 7:
        return None
    return sha


async def _refresh() -> int:
    """Walk the repos dir, update drifted rows. Return process exit code."""
    if not REPOS_ROOT.exists():
        print(
            f"[refresh-repo-pack] repos root {REPOS_ROOT} missing — nothing to do"
        )
        return 0

    # Import lazily so a CI runner without the API deps installed can
    # still ``python -c`` the help text without ImportError.
    # The script lives outside ``apps/api`` so we add it to sys.path
    # explicitly before importing.
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

    try:
        from sqlalchemy import select, update
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import get_settings
        from app.models.repo_pack import RepoPack
    except Exception as exc:
        print(f"[refresh-repo-pack] API package import failed: {exc}")
        # Cannot resolve DB connection without the API package — fail
        # hard so the entrypoint surfaces the structural misconfig.
        return 2

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True)
    session_local = async_sessionmaker(bind=engine, expire_on_commit=False)

    changes: list[tuple[str, str, str]] = []
    try:
        async with session_local() as db:
            for pack_dir in sorted(p for p in REPOS_ROOT.iterdir() if p.is_dir()):
                sha = _git_head_sha(pack_dir)
                if sha is None:
                    continue
                row = (
                    await db.execute(
                        select(RepoPack).where(RepoPack.id == pack_dir.name)
                    )
                ).scalar_one_or_none()
                if row is None:
                    # Pack on disk has no DB row yet — emit a warning so
                    # the operator notices, but do not block the boot.
                    print(
                        f"[refresh-repo-pack] {pack_dir.name}: no DB row "
                        f"(disk SHA={sha[:12]})"
                    )
                    continue
                if (row.repo_sha or "") == sha:
                    continue
                before = row.repo_sha or "(empty)"
                await db.execute(
                    update(RepoPack)
                    .where(RepoPack.id == pack_dir.name)
                    .values(repo_sha=sha)
                )
                changes.append((pack_dir.name, before, sha))
            await db.commit()
    except Exception as exc:
        # A DB connection failure is the one case we want to exit
        # non-zero on so the deploy aborts before alembic runs against
        # a broken connection string.
        print(f"[refresh-repo-pack] database operation failed: {exc}")
        return 1
    finally:
        await engine.dispose()

    if not changes:
        print("[refresh-repo-pack] no SHA drift detected")
        return 0
    for pack_id, before, after in changes:
        print(
            f"[refresh-repo-pack] {pack_id}: {before[:12]} → {after[:12]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_refresh()))
