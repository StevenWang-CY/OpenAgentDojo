"""One-shot backfill: stamp verification hash + signature on historical submissions.

Run after migration 0019 lands so already-graded rows from before P0-11
can be verified via ``/verify/{submission_id}``. The script is idempotent
— a row that already carries both columns is left untouched (unless
``--reseal`` is passed, in which case the signature is recomputed under
the current secret and the hash is verified to still match; mismatches
abort the run loudly so an operator can investigate).

Usage::

    cd apps/api
    uv run python -m scripts.backfill_verification --apply
    uv run python -m scripts.backfill_verification --apply --reseal

Without ``--apply`` the script does a dry run that reports the rows it
would touch but does not write to the DB. The default is dry-run so a
mis-pointed env cannot accidentally re-stamp production rows.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Make ``app.*`` importable when this is run as ``python -m scripts.``
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.missions.cache import cached_manifests
from app.missions.service import get_mission as get_mission_row
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.reports.verification import (
    build_envelope,
    compute_hash,
    compute_signature,
    verify_secret,
)

logger = logging.getLogger("backfill_verification")


async def _stamp_one(
    db: AsyncSession,
    submission: Submission,
    *,
    secret: str,
    apply: bool,
    reseal: bool,
) -> str:
    session = (
        await db.execute(select(SessionRow).where(SessionRow.id == submission.session_id))
    ).scalar_one_or_none()
    if session is None:
        return "skip(no-session)"
    if session.status != "graded":
        return "skip(not-graded)"

    user_row = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()
    mission_row = await get_mission_row(db, session.mission_id)
    loaded = cached_manifests().get(session.mission_id)
    manifest = loaded.manifest if loaded is not None else None

    envelope = build_envelope(
        submission=submission,
        session=session,
        manifest=manifest,
        user=user_row,
        mission_row=mission_row,
    )
    new_hash = compute_hash(envelope)
    new_sig = compute_signature(new_hash, secret)

    has_hash = bool(submission.verification_hash)
    has_sig = bool(submission.verification_signature)

    if has_hash and has_sig and not reseal:
        return "skip(already-stamped)"

    if reseal and has_hash and submission.verification_hash != new_hash:
        # Re-derived hash differs from the stored one → mission folder
        # changed or the envelope schema bumped without coordination.
        # Refuse to overwrite; an operator must investigate.
        logger.error(
            "submission %s: stored hash %s does not match re-derived hash %s; "
            "refusing to overwrite under --reseal",
            submission.id,
            submission.verification_hash[:12],
            new_hash[:12],
        )
        return "skip(hash-mismatch)"

    if not apply:
        return "would-stamp"

    submission.verification_hash = new_hash
    submission.verification_signature = new_sig
    await db.flush()
    return "stamped"


async def _main(apply: bool, reseal: bool, limit: int | None) -> int:
    settings = get_settings()
    secret = verify_secret(settings)

    async with AsyncSessionLocal() as db:
        stmt = select(Submission).where(Submission.total_score.is_not(None))
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await db.execute(stmt)).scalars().all()

        outcomes: dict[str, int] = {}
        for row in rows:
            result = await _stamp_one(
                db, row, secret=secret, apply=apply, reseal=reseal
            )
            outcomes[result] = outcomes.get(result, 0) + 1

        if apply:
            await db.commit()

        logger.info("backfill complete: %s", outcomes)
        return 0 if outcomes.get("skip(hash-mismatch)", 0) == 0 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to the DB (default: dry-run, no writes).",
    )
    parser.add_argument(
        "--reseal",
        action="store_true",
        help=(
            "Re-derive the signature for already-stamped rows under the "
            "CURRENT verify secret. Hash must still match; mismatches abort."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of rows touched (useful for smoke tests).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG/INFO/WARNING/ERROR).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    rc = asyncio.run(_main(apply=args.apply, reseal=args.reseal, limit=args.limit))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
