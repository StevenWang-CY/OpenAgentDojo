"""Queue processor (Mission 08).

The contract is "exactly-once across concurrent workers":

  * many workers may call ``process_job(session_factory, job_id)`` for the
    same id at the same time; **at most one** should observe the
    pending->running transition
  * the others should return ``ProcessResult(claimed=False)`` without
    raising
  * after one worker succeeds, the row is ``done`` and ``processed_at`` is set

The current ``claim_job`` is buggy: it reads the row, branches on its
status, then writes back. The implicit transaction commits twice and
two concurrent workers can both observe ``pending`` before either
writes. The fix is a single transactional UPDATE filtered on
``status == 'pending'`` — see ``docs/jobs.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import Job


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of one ``process_job`` invocation.

    ``claimed`` is True iff *this* worker won the race. Other workers see
    ``claimed=False`` (and ``done=True`` once any worker has finished).
    """

    claimed: bool
    done: bool


async def claim_job(session: AsyncSession, job_id: int) -> bool:
    """Try to flip a pending job to running. Return True if we won.

    BUG (Mission 08): this is a check-then-act. Two concurrent callers
    can both observe ``status == 'pending'`` before either writes, and
    both will then UPDATE to ``running`` — both come back ``claimed=True``.
    """
    job = await session.get(Job, job_id)
    if job is None or job.status != "pending":
        return False
    # We "released" the read transaction by exiting `get()`'s implicit
    # snapshot; now we UPDATE without re-checking the guard. Two workers
    # can both reach this line.
    stmt = update(Job).where(Job.id == job_id).values(status="running")
    await session.execute(stmt)
    await session.commit()
    return True


async def finish_job(session: AsyncSession, job_id: int) -> None:
    """Mark a claimed job as done. Always commits."""
    stmt = (
        update(Job)
        .where(Job.id == job_id)
        .values(status="done", processed_at=datetime.now(timezone.utc))
    )
    await session.execute(stmt)
    await session.commit()


async def process_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> ProcessResult:
    """Try to claim and process the job. Return whether we did.

    Idempotent **only when** ``claim_job`` is atomic. With the shipped
    check-then-act, callers can both come back ``claimed=True`` under
    load — that's Mission 08.
    """
    async with session_factory() as session:
        won = await claim_job(session, job_id)

    if not won:
        return ProcessResult(claimed=False, done=False)

    async with session_factory() as session:
        await finish_job(session, job_id)

    return ProcessResult(claimed=True, done=True)


# Re-export the select symbol so callers don't have to import from sqlalchemy
# directly; keeps the jobs module the single entry-point for queue work.
__all__ = ["ProcessResult", "claim_job", "finish_job", "process_job", "select"]
