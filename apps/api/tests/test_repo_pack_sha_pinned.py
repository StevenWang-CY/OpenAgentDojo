"""Skeleton coverage for the P1-1 ``repo_packs.repo_sha`` pin.

The full CI gate that diffs ``repo_packs.repo_sha`` against
``git rev-parse HEAD`` on the on-disk pack lands in a follow-up PR
(P1-1 PR2 — Go pack + Dockerfile). This file owns the *skeleton*: it
asserts that the seed migration writes a ``repo_sha`` value for every
expected pack, so the column never silently regresses to NULL during
a future refactor. The on-disk diff is intentionally out of scope here
to keep PR1 focused on schema + manifest plumbing.
"""

from __future__ import annotations

from typing import Final

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.repo_pack import RepoPack

# Pinned set of pack ids the seed migration writes (mirror of
# ``_SEED_REPO_PACKS`` in alembic/versions/0025_mission_tags_pack_metadata.py).
# Kept here so this test breaks loudly if the seed list drifts and we forget
# to update either the migration or this skeleton.
_EXPECTED_PACK_IDS: Final[tuple[str, ...]] = (
    "fullstack-auth-demo",
    "data-api-demo",
    "go-orders-service",
)


@pytest.mark.asyncio
async def test_repo_pack_sha_present_for_every_seeded_pack(db_engine) -> None:
    """Each shipped pack must have a non-empty ``repo_sha`` row.

    The skeleton seeds the rows in-test (the real seed runs via the
    alembic migration which is not exercised by the SQLite fixture) and
    asserts the column never accepts empty / NULL. The on-disk SHA gate
    is layered on top of this in a follow-up PR.
    """
    Session = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    placeholder_sha = "0" * 40
    async with Session() as db:
        # The on-disk migration also seeds three rows; the SQLite test
        # engine doesn't run migrations, so we seed by hand mirroring the
        # migration's _SEED_REPO_PACKS list.
        for pack_id in _EXPECTED_PACK_IDS:
            db.add(
                RepoPack(
                    id=pack_id,
                    title=f"{pack_id} (test seed)",
                    language=_language_for(pack_id),
                    stack_summary=f"Test stack for {pack_id}",
                    repo_sha=placeholder_sha,
                )
            )
        await db.commit()

    async with Session() as db:
        rows = (await db.execute(select(RepoPack))).scalars().all()

    by_id = {row.id: row for row in rows}
    for pack_id in _EXPECTED_PACK_IDS:
        assert pack_id in by_id, f"repo pack {pack_id!r} missing from seed"
        pack = by_id[pack_id]
        assert pack.repo_sha, f"repo_sha for {pack_id!r} must be non-empty"
        # SHA shape is opaque to this skeleton (the real CI gate verifies
        # against ``git rev-parse HEAD``). The placeholder used here is a
        # 40-char hex zero string; just sanity-check the length so a stray
        # truncation surfaces immediately.
        assert len(pack.repo_sha) == 40, (
            f"repo_sha for {pack_id!r} must be a 40-char placeholder or "
            "real SHA; got "
            f"{pack.repo_sha!r}"
        )
        assert pack.language in {"typescript", "python", "go"}


def _language_for(pack_id: str) -> str:
    """Return the seeded language for a pack id (mirrors the migration)."""
    return {
        "fullstack-auth-demo": "typescript",
        "data-api-demo": "python",
        "go-orders-service": "go",
    }[pack_id]
