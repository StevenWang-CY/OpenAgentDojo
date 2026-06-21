"""Scheduled hard-delete worker for accounts past their 7-day grace (P0-6).

Invoked by ``apps/api/scripts/process_deletion_grace.py`` (e.g. via cron
once per day). Walks every user whose ``deletion_scheduled_at`` is in
the past and:

1. Marks any active session abandoned (so the orphan sweeper does not
   resurrect a sandbox that's about to be deleted).
2. Hard-deletes every per-user row via the ``ON DELETE CASCADE`` chain
   from ``users``.
3. Removes any data-export S3 objects (``ON DELETE CASCADE`` drops the
   row but not the bucket object).
4. Tombstones the row: ``email = deleted-{uuid_hex}@deleted.openagentdojo.app``,
   ``handle = deleted-{uuid_hex}``, ``display_name = None``,
   ``github_login = None``, ``deletion_scheduled_at = None``,
   ``session_epoch += 1`` (any lingering cookies die).

   The tombstone uses the *full* 32-char UUID hex (not a short prefix) on
   both the ``email`` and ``handle`` columns. Both columns are UNIQUE; an
   8-char prefix has a birthday-paradox collision around ~65k accounts —
   each collision then raised ``IntegrityError`` inside the worker, the
   outer try/except rolled back, and the user was never deleted (silent
   indefinite retry loop). 32 hex chars give us a 128-bit collision space,
   making accidental collision astronomically unlikely while staying well
   within the CITEXT capacity.

The function returns the number of accounts processed in this pass so
the CLI can print a sensible exit status / log line.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Final

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.data_export import DataExport
from app.models.report_render import ReportRender
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.observability import (
    account_deletion_grace_run_total,
    account_deletions_processed_total,
    llm_cache_shared_row_retained_total,
)
from app.storage import delete_object

# Tombstone email domain — moved to settings so dev / staging / production
# can each carry their own sentinel (avoids any chance of a real ``@deleted
# .openagentdojo.app`` address ever being deliverable). The literal here
# is the production default and matches the historical value so existing
# tombstone rows remain valid.
_DELETED_DOMAIN_FALLBACK: Final = "deleted.openagentdojo.app"


def _tombstone_domain() -> str:
    """Resolve the tombstone domain from settings, falling back to the historical default."""
    domain = getattr(get_settings(), "deleted_tombstone_domain", None)
    if isinstance(domain, str) and domain.strip():
        return domain.strip()
    return _DELETED_DOMAIN_FALLBACK


def process_deletion_grace() -> int:
    """Sync wrapper around :func:`_async_process_deletion_grace`.

    CLI-friendly entry point; the cron script imports this directly.
    Emits the ``account_deletion_grace_run_total`` counter exactly once
    per invocation labelled by outcome — see the script docstring.
    """
    return asyncio.run(_async_process_deletion_grace())


async def _async_process_deletion_grace() -> int:
    """Process every expired-grace user; return the count actually deleted.

    Outcome accounting:
      * ``failed`` — DB unreachable (no rows fetched, no work attempted).
      * ``partial`` — at least one row processed AND at least one raised.
      * ``success`` — every fetched row deleted cleanly (count may be 0
        on an empty pass, which is still ``success`` — the sweeper ran).

    The counter increments exactly once per invocation so the alerting
    rule "no success tick in 36h" trivially catches a wedged cron job.
    """
    from app.db.session import AsyncSessionLocal

    now = datetime.now(UTC)
    processed = 0
    failed = 0

    try:
        async with AsyncSessionLocal() as db:
            users = await _fetch_expired_users(db, now)
            for user in users:
                try:
                    await _hard_delete_user(db, user)
                    processed += 1
                    account_deletions_processed_total.inc()
                except Exception as exc:
                    failed += 1
                    logger.exception(
                        "account_deletion: hard-delete failed for {}: {}",
                        user.id,
                        exc,
                    )
                    await db.rollback()
    except Exception as exc:
        # DB / connection failure before we touched any row — distinct
        # from per-row failures (which produce ``partial``).
        logger.exception("account_deletion: sweeper aborted before any row: {}", exc)
        account_deletion_grace_run_total.labels(result="failed").inc()
        raise

    if failed == 0:
        result = "success"
    elif processed > 0:
        result = "partial"
    else:
        # Rows existed but every single one raised — surface as failed so
        # the alert fires. The counter and the log line carry the same
        # signal.
        result = "failed"
    account_deletion_grace_run_total.labels(result=result).inc()

    if processed:
        logger.info(
            "account_deletion: hard-deleted {} account(s) (failed={}, result={})",
            processed,
            failed,
            result,
        )
    elif failed:
        logger.warning(
            "account_deletion: 0 rows hard-deleted, {} failure(s), result={}",
            failed,
            result,
        )
    return processed


async def _fetch_expired_users(db: AsyncSession, now: datetime) -> list[User]:
    stmt = select(User).where(
        User.deletion_scheduled_at.is_not(None),
        User.deletion_scheduled_at <= now,
    )
    return list((await db.execute(stmt)).scalars())


async def _hard_delete_user(db: AsyncSession, user: User) -> None:  # noqa: PLR0912, PLR0915
    # This worker fans out across every per-user table in lockstep with
    # the CASCADE chain plus a handful of resources that live outside
    # the relational graph (S3 objects, scratchpad coaching cache rows).
    # Splitting it further would scatter the section ordering that the
    # comments document; the structure is more legible as one method.
    user_id = user.id

    # ----- 1. Mark active sessions abandoned --------------------------------
    sessions = list(
        (
            await db.execute(
                select(SessionRow).where(
                    SessionRow.user_id == user_id,
                    SessionRow.status.in_(("active", "provisioning", "submitting")),
                )
            )
        ).scalars()
    )
    for sess in sessions:
        sess.status = "abandoned"
        sess.completed_at = datetime.now(UTC)
    if sessions:
        await db.flush()

    # ----- 2. Delete every S3 object owned by the user ---------------------
    exports = list(
        (
            await db.execute(
                select(DataExport).where(
                    DataExport.user_id == user_id,
                    DataExport.s3_key.is_not(None),
                )
            )
        ).scalars()
    )
    for export in exports:
        if export.s3_key:
            try:
                delete_object(export.s3_key)
            except Exception as exc:
                # Don't abort the whole deletion on a single S3 failure —
                # we'll re-attempt on the next worker pass via the row's
                # presence. Log loudly so ops can clean up the orphan.
                logger.warning(
                    "account_deletion: could not delete export {} key={} ({})",
                    export.id,
                    export.s3_key,
                    exc,
                )

    # Phase 4.A.10 — also drop every ``report_renders.s3_key`` owned by
    # this user. CASCADE on ``ON DELETE`` removes the row, but S3 lives
    # outside the relational graph; without an explicit ``delete_object``
    # the PDF/PNG bytes linger in the bucket forever. We walk the chain
    # ``users → sessions → submissions → report_renders`` so a SQL JOIN
    # stays linear in user state and doesn't accidentally pick up
    # another user's renders.
    render_rows = list(
        (
            await db.execute(
                select(ReportRender)
                .join(Submission, Submission.id == ReportRender.submission_id)
                .join(SessionRow, SessionRow.id == Submission.session_id)
                .where(
                    SessionRow.user_id == user_id,
                    ReportRender.s3_key.is_not(None),
                )
            )
        ).scalars()
    )
    for render in render_rows:
        if render.s3_key:
            try:
                delete_object(render.s3_key)
                logger.info(
                    "account_deletion: deleted report-render s3_key={} render_id={}",
                    render.s3_key,
                    render.id,
                )
            except Exception as exc:
                logger.warning(
                    "account_deletion: could not delete report-render {} key={} ({})",
                    render.id,
                    render.s3_key,
                    exc,
                )

    # ----- 3. Cascade-delete per-user rows + 4. tombstone ------------------
    # Use the full 32-char UUID hex (not a prefix) so the tombstone email +
    # handle are mathematically guaranteed unique across the bucket of
    # deleted accounts — a prefix would birthday-collide around ~65k
    # tombstones, raise IntegrityError on the UNIQUE constraints, and pin
    # the user in a silent retry loop without ever being deleted.
    tombstone_id = user_id.hex
    user.email = f"deleted-{tombstone_id}@{_tombstone_domain()}"
    user.handle = f"deleted-{tombstone_id}"
    user.display_name = None
    user.github_login = None
    user.pending_email = None
    user.deletion_scheduled_at = None
    user.session_epoch = (user.session_epoch or 1) + 1
    # Clear tutorial / login state so the tombstone row carries nothing
    # beyond what's required to prove "this id existed and was deleted".
    user.last_login_at = None
    user.tutorial_completed_at = None

    # The CASCADE chain on FKs to ``users.id`` clears sessions, magic
    # links, badges, consents, data_exports, etc. — but the user row
    # itself stays as a tombstone, so the FK fan-out runs only against
    # *removed* descendants when we issue per-table deletes below. We
    # rely on Postgres CASCADE to do the actual fan-out at the row level.
    # Force the cascade by re-issuing deletes on the immediate children
    # so SQLite (which does not always honour application-time CASCADE
    # on existing rows) still ends up clean for tests.
    from sqlalchemy import delete as sa_delete

    from app.models.agent_turn import AgentTurn
    from app.models.coaching_cache_user_index import CoachingCacheUserIndex
    from app.models.command_run import CommandRun
    from app.models.file_change import FileChange
    from app.models.llm_cache import LLMCache
    from app.models.magic_link_token import MagicLinkToken
    from app.models.session_note import SessionNote
    from app.models.supervision_event import SupervisionEvent
    from app.models.user_badge import UserBadge
    from app.models.user_recommendation import UserRecommendation

    user_session_ids = list(
        (await db.execute(select(SessionRow.id).where(SessionRow.user_id == user_id))).scalars()
    )
    if user_session_ids:
        for child_model in (
            AgentTurn,
            FileChange,
            CommandRun,
            SupervisionEvent,
            # P1-4 — scratchpad bodies. ``ON DELETE CASCADE`` on
            # session_notes.session_id handles this on Postgres, but
            # SQLite ignores app-time CASCADE unless ``PRAGMA
            # foreign_keys = ON`` is explicitly set on the connection.
            # Re-issue the delete here so the test path stays clean
            # and a refactor that drops the cascade later still wipes
            # the user's private notes on hard-delete.
            SessionNote,
        ):
            await db.execute(
                sa_delete(child_model).where(child_model.session_id.in_(user_session_ids))
            )
        # Phase 4.A.10 — drop report_renders before submissions so the
        # FK chain stays clean on SQLite (which doesn't honour cascade
        # at application time). The S3 objects were already removed in
        # the explicit ``delete_object`` loop above.
        submission_ids = list(
            (
                await db.execute(
                    select(Submission.id).where(Submission.session_id.in_(user_session_ids))
                )
            ).scalars()
        )
        if submission_ids:
            await db.execute(
                sa_delete(ReportRender).where(ReportRender.submission_id.in_(submission_ids))
            )
        await db.execute(sa_delete(Submission).where(Submission.session_id.in_(user_session_ids)))
        await db.execute(sa_delete(SessionRow).where(SessionRow.user_id == user_id))

    await db.execute(sa_delete(MagicLinkToken).where(MagicLinkToken.user_id == user_id))
    await db.execute(sa_delete(UserBadge).where(UserBadge.user_id == user_id))
    # P1-2 — the materialised recommendation cache is keyed on users.id with
    # ``ON DELETE CASCADE``, but the tombstone keeps the users row alive so
    # the cascade never fires; without this explicit delete the row survives
    # erasure forever (GDPR residual). Re-issue the delete here like the
    # other user-scoped children above.
    # P1-2 — the materialised recommendation cache is keyed on users.id with
    # ``ON DELETE CASCADE``, but the tombstone keeps the users row alive so
    # the cascade never fires; without this explicit delete the row survives
    # erasure forever (GDPR residual). Re-issue the delete here like the
    # other user-scoped children above.
    await db.execute(sa_delete(UserRecommendation).where(UserRecommendation.user_id == user_id))
    await db.execute(sa_delete(DataExport).where(DataExport.user_id == user_id))

    # P1-4 — wipe scratchpad-coaching cache rows produced by this user.
    # The chain is users → coaching_cache_user_index → llm_cache. We
    # MUST NOT delete a cache row that any OTHER user's index still
    # references — that other user would lose their cache for no
    # reason, and the chokepoint would have to regenerate from
    # Bedrock on their next request (privacy is preserved either way
    # because the cache key never carries a user id, only content
    # hashes). The per-row check below preserves shared rows and
    # surfaces the count on a counter so ops can verify the
    # contract holds under real traffic.
    coaching_cache_ids = list(
        (
            await db.execute(
                select(CoachingCacheUserIndex.llm_cache_id).where(
                    CoachingCacheUserIndex.user_id == user_id
                )
            )
        ).scalars()
    )
    if coaching_cache_ids:
        to_delete: list = []
        retained = 0
        for cache_id in coaching_cache_ids:
            others = (
                await db.execute(
                    select(CoachingCacheUserIndex.user_id)
                    .where(
                        CoachingCacheUserIndex.llm_cache_id == cache_id,
                        CoachingCacheUserIndex.user_id != user_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if others is None:
                to_delete.append(cache_id)
            else:
                retained += 1
        if to_delete:
            await db.execute(sa_delete(LLMCache).where(LLMCache.id.in_(to_delete)))
        if retained:
            llm_cache_shared_row_retained_total.inc(retained)
            logger.info(
                "account_deletion: retained {} shared coaching cache row(s) for user {}",
                retained,
                user_id,
            )
    # Always drop the user's index rows — those are user-scoped by
    # definition; preserving them would orphan-point at deleted rows.
    await db.execute(
        sa_delete(CoachingCacheUserIndex).where(CoachingCacheUserIndex.user_id == user_id)
    )

    await _wipe_consent_history_and_stamp_tombstone(db, user_id, user.handle)

    await db.commit()


async def _wipe_consent_history_and_stamp_tombstone(
    db: AsyncSession, user_id, tombstone_handle: str
) -> None:
    """Drop the user's consent + account-event history and stamp the deletion marker.

    Extracted from :func:`_hard_delete_user` to keep the parent function
    under the linter's statement budget and to isolate the
    migration-sensitive import. Narrow the except to ``ImportError`` so a
    runtime SQL failure surfaces loudly instead of being silently
    swallowed (the prior bare-``Exception`` swallowed real DB errors).

    Migration 0018 widens the ``account_events_type_check`` CHECK to allow
    the ``account.deleted`` literal staged below; older deployments
    without the migration will fail on insert, which is the correct
    failure mode (the worker logs + rolls back).
    """
    from sqlalchemy import delete as sa_delete

    try:
        from app.models.user_consent import AccountEvent, UserConsent
    except ImportError:  # pragma: no cover — old deploy before 0015/0017
        return

    # Drop the historical consent + event rows first — they're scoped
    # to the live account and the tombstone has no use for them.
    await db.execute(sa_delete(AccountEvent).where(AccountEvent.user_id == user_id))
    await db.execute(sa_delete(UserConsent).where(UserConsent.user_id == user_id))
    # Stage the terminal account event INSIDE the same transaction as
    # the cascade + tombstone so the audit log can never claim a
    # deletion completed when the row write rolled back, or vice versa.
    db.add(
        AccountEvent(
            user_id=user_id,
            event_type="account.deleted",
            payload={"tombstone_handle": tombstone_handle},
        )
    )


__all__ = ["process_deletion_grace"]
