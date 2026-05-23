"""Session creation, status transitions, and queries.

``set_status`` and ``set_sandbox`` flush + commit themselves so background
workers (provisioning, grading) don't need to remember to call ``commit()``
after every mutation. Request-scoped callers can still rely on the
``get_db`` dependency's outer commit — the inner commit becomes a no-op when
the transaction was already flushed within the same unit of work.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mission import Mission
from app.models.session import SessionRow

SessionStatus = Literal["provisioning", "active", "submitting", "graded", "abandoned", "error"]

# Statuses considered "live" for the per-user concurrency cap (§21 — MVP allows
# 1 active session per user). A session in any of these states blocks the same
# user from starting another one until it transitions to graded/abandoned/error.
_LIVE_SESSION_STATUSES: frozenset[str] = frozenset({"provisioning", "active", "submitting"})


class MissionNotFoundError(LookupError):
    pass


class SessionNotFoundError(LookupError):
    """Raised when a service helper is invoked for a session id that no longer exists.

    Background workers used to swallow this silently, which masked race
    conditions (provisioner writing back to a session that was already reaped
    by the orphan sweeper). Surfacing it as a typed exception lets each
    caller decide whether to log + ignore or escalate.
    """

    def __init__(self, session_id: uuid.UUID) -> None:
        super().__init__(str(session_id))
        self.session_id = session_id


class ActiveSessionExistsError(Exception):
    """Raised when a user tries to start a second concurrent session.

    Carries the ``id`` of the existing live session so the router can surface it
    to the client in the 409 response payload.
    """

    def __init__(self, active_session_id: uuid.UUID) -> None:
        super().__init__(str(active_session_id))
        self.active_session_id = active_session_id


async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    mission_id: str,
) -> SessionRow:
    """Create a new session for ``user_id`` against ``mission_id``.

    The caller is responsible for guaranteeing ``user_id`` corresponds to an
    existing ``users`` row — the auth dependency (``require_auth``) does this
    in production, and the dev-only fallback in ``auth/deps.py`` upserts a
    placeholder. We no longer synthesise a user here.
    """
    mission = (
        await db.execute(select(Mission).where(Mission.id == mission_id))
    ).scalar_one_or_none()
    if mission is None:
        raise MissionNotFoundError(mission_id)

    # Per-user concurrency cap (§21 — MVP: 1 active session at a time).
    # Reject the second concurrent session at the service layer with a
    # custom exception that the router converts to HTTP 409.
    existing_id = (
        await db.execute(
            select(SessionRow.id)
            .where(SessionRow.user_id == user_id)
            .where(SessionRow.status.in_(_LIVE_SESSION_STATUSES))
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        raise ActiveSessionExistsError(existing_id)

    row = SessionRow(
        user_id=user_id,
        mission_id=mission_id,
        status="provisioning",
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # Two concurrent POST /sessions from the same user can both pass
        # the SELECT above and race to the INSERT — the rate-limit budget
        # (6/min/user) keeps the blast radius small but the §21 cap is
        # logical, not enforced by a DB constraint. Roll back the failed
        # insert and re-fetch the now-live row so the second caller sees
        # the same 409 envelope as the sequential case.
        await db.rollback()
        racing_id = (
            await db.execute(
                select(SessionRow.id)
                .where(SessionRow.user_id == user_id)
                .where(SessionRow.status.in_(_LIVE_SESSION_STATUSES))
                .limit(1)
            )
        ).scalar_one_or_none()
        if racing_id is not None:
            raise ActiveSessionExistsError(racing_id)
        raise
    return row


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> SessionRow | None:
    return (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one_or_none()


async def set_status(db: AsyncSession, session_id: uuid.UUID, status: SessionStatus) -> None:
    """Update ``status`` and commit.

    Background workers call this without owning an outer transaction so we
    commit here. Request-scoped callers operating inside ``get_db`` should
    avoid calling this (mutate the ORM object directly and let get_db commit
    once) but the double-commit is harmless: SQLAlchemy treats a commit on a
    session with no pending changes as a no-op.
    """
    row = await get_session(db, session_id)
    if row is None:
        return
    row.status = status
    await db.flush()
    await db.commit()


async def set_sandbox(db: AsyncSession, session_id: uuid.UUID, sandbox_id: str) -> None:
    """Update ``sandbox_id`` and commit. See ``set_status`` for semantics.

    Raises :class:`SessionNotFoundError` when ``session_id`` does not resolve to
    an existing row — previously this silently no-op'd, which masked the
    provision-after-reap race condition that left WS terminals pointing at a
    zombie handle.
    """
    row = await get_session(db, session_id)
    if row is None:
        raise SessionNotFoundError(session_id)
    row.sandbox_id = sandbox_id
    await db.flush()
    await db.commit()
