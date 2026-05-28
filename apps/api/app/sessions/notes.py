"""Per-session scratchpad endpoints (P1-4).

Three routes mounted under ``/sessions``:

* ``GET  /sessions/{id}/note`` — fetch the current body. Returns an
  empty body if no row exists (DOES NOT insert; we want a fresh
  session's read to be cheap and side-effect free).
* ``PUT  /sessions/{id}/note`` — upsert the body and emit a coalesced
  ``note.edited`` supervision event.
* ``POST /sessions/{id}/events/note-viewed`` — record a
  ``note.viewed_during_prompt`` event (no coalescing — the event is
  rare by construction; the FE fires it only on composer focus while
  the scratchpad has content).

Coalescing — the load-bearing helper :func:`_maybe_coalesce` updates
the last ``note.edited`` event in place when it fired within the past
30 seconds, otherwise emits a new one. The contract is "every PUT
durably persists the body; the supervision-event row count is the
de-noised view of edit *bursts*, not keystroke fidelity".
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_auth
from app.db.session import get_db
from app.models.session_note import SessionNote
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.observability import note_edited_burst_seconds_clamp_total
from app.schemas.notes import (
    NOTE_BODY_MAX_BYTES,
    NoteViewedDuringPromptBody,
    SessionNoteRead,
    SessionNoteWrite,
)
from app.sessions.events import _PENDING_KEY, EventEmitter, get_redis
from app.sessions.router import _require_mutable_session, _require_owned_session

router = APIRouter(prefix="/sessions", tags=["sessions"])


# Coalescing window for ``note.edited`` events. PUTs landing within
# this window update the latest event row in place rather than
# emitting a new one — see the rationale in P1_DESIGN.md §P1-4
# ("Coalescing within 30 s is the right balance…").
_COALESCE_WINDOW = timedelta(seconds=30)


def _dialect_name(db: AsyncSession) -> str | None:
    """Best-effort dialect name lookup (Postgres vs. SQLite test path)."""
    try:
        engine = db.get_bind()
    except Exception:
        return None
    if engine is None:
        return None
    dialect = getattr(engine, "dialect", None)
    if dialect is None:
        return None
    name = getattr(dialect, "name", None)
    return str(name) if isinstance(name, str) else None


def _as_utc(value: datetime) -> datetime:
    """Normalise a possibly-naive timestamp (SQLite) to UTC-aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _line_count(body: str) -> int:
    """Return the count of lines (1 for a single line, 0 for empty)."""
    if not body:
        return 0
    return body.count("\n") + 1


async def _upsert_note(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    body: str,
    updated_at: datetime,
) -> SessionNote:
    """Idempotent upsert of one scratchpad row.

    Uses Postgres' ``ON CONFLICT DO UPDATE`` on the PK; the SQLite test
    path emulates with a SELECT-then-write. Mirrors the pattern in
    ``apps/api/app/recommendations/cache.py::_upsert_row``.
    """
    dialect = _dialect_name(db)
    if dialect == "postgresql":
        stmt = pg_insert(SessionNote).values(
            session_id=session_id,
            body=body,
            updated_at=updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id"],
            set_={
                "body": body,
                "updated_at": updated_at,
            },
        )
        await db.execute(stmt)
        await db.flush()
        row = (
            await db.execute(
                select(SessionNote).where(SessionNote.session_id == session_id)
            )
        ).scalar_one()
        return row

    existing = (
        await db.execute(
            select(SessionNote).where(SessionNote.session_id == session_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        row = SessionNote(
            session_id=session_id,
            body=body,
            updated_at=updated_at,
        )
        db.add(row)
        await db.flush()
        return row
    existing.body = body
    existing.updated_at = updated_at
    await db.flush()
    return existing


async def _latest_note_edited_event(
    db: AsyncSession, session_id: uuid.UUID
) -> SupervisionEvent | None:
    """Return the most-recent ``note.edited`` event for the session, or None."""
    stmt = (
        select(SupervisionEvent)
        .where(
            SupervisionEvent.session_id == session_id,
            SupervisionEvent.event_type == "note.edited",
        )
        .order_by(SupervisionEvent.occurred_at.desc(), SupervisionEvent.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# Defensive upper bound for the seconds_since_last_edit computation. A
# session can legitimately span many hours (the supervision experience
# is intentionally long-form), so we don't clamp at minutes — but a 30-day
# value almost certainly means a clock jumped or a stale event row leaked
# in via a replay. Anything above this is treated as "previous edit was so
# long ago we may as well report it as zero" and the
# :data:`note_edited_burst_seconds_clamp_total` counter is bumped so the
# anomaly surfaces in ops.
_SECONDS_SINCE_LAST_EDIT_MAX = 30 * 24 * 60 * 60  # 30 days.


def _build_note_edited_payload(
    *,
    body: str,
    now: datetime,
    previous_event_occurred_at: datetime | None,
) -> dict[str, Any]:
    """Materialise the ``note.edited`` payload from the post-write state.

    ``seconds_since_last_edit`` is the elapsed seconds between the
    *previous* ``note.edited`` supervision event (the de-noised burst
    timeline the FE renders) and ``now``. We deliberately do NOT read
    from the scratchpad row's ``updated_at`` here — inside a coalescing
    burst the row is upserted on every PUT, so a row-based delta is
    always ~0 and the user-visible "you paused N seconds before editing
    again" signal collapses. Reading from the supervision-event row
    gives the correct "time since the previous burst start" semantics.

    Edge cases:

    * ``previous_event_occurred_at is None`` — first ``note.edited`` event
      for this session; report 0 (documented behaviour).
    * negative or absurd delta (>30 days) — clamp to 0 and bump
      ``note_edited_burst_seconds_clamp_total`` so the clock-skew /
      stale-row anomaly is observable in dashboards.
    """
    if previous_event_occurred_at is None:
        seconds_since = 0
    else:
        raw = int((now - _as_utc(previous_event_occurred_at)).total_seconds())
        if raw < 0 or raw > _SECONDS_SINCE_LAST_EDIT_MAX:
            note_edited_burst_seconds_clamp_total.inc()
            seconds_since = 0
        else:
            seconds_since = raw
    return {
        "bytes": len(body.encode("utf-8")),
        "lines": _line_count(body),
        "seconds_since_last_edit": seconds_since,
    }


async def _coalesce_or_emit_note_edited(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    body: str,
    now: datetime,
) -> dict[str, Any]:
    """Update the most-recent ``note.edited`` event in place when it lives
    inside the coalescing window; otherwise emit a fresh one. Returns the
    payload that was persisted so the route handler can log it.

    The coalesced row's payload reflects the FINAL state (bytes/lines)
    and a freshly-computed ``seconds_since_last_edit`` (delta against
    the burst's prior event), but the row's ``occurred_at`` is left at
    the burst-start timestamp so the post-mortem timeline reads the
    burst at its inception, not at its tail. This matches the design
    contract that the supervision-event row count is "one event per
    edit burst, anchored at the burst start" — shifting ``occurred_at``
    forward on every PUT would compress the burst into a single instant
    at the end of the burst and break the FE timeline's gap rendering.

    The previous-event-occurred-at used to derive
    ``seconds_since_last_edit`` is the occurred_at of the
    *previous* ``note.edited`` event row (NOT the latest, which is
    typically the one we are about to coalesce into — that would
    always yield ~0). For the emit branch (no recent event), the
    latest row IS the previous one, so we use it directly.

    Coalesce path WS contract — we re-publish the mutated row on the
    same Redis channel the live emit uses, keeping the SAME event id.
    Subscribers that already received id=N on the first edit get the
    refreshed payload (but unchanged occurred_at) on the second; without
    the publish the WS-rendered timeline drifts from the DB-rendered one
    until the subscriber reconnects + backfills. The publish is queued
    via the same deferred mechanism the EventEmitter uses so it only
    goes out after the request's transaction commits.
    """
    latest = await _latest_note_edited_event(db, session_id)
    if latest is not None:
        latest_at = _as_utc(latest.occurred_at)
        if now - latest_at <= _COALESCE_WINDOW:
            # Inside the coalescing window: derive seconds_since against
            # the event *before* ``latest`` (the burst's predecessor),
            # not against ``latest`` itself — otherwise the delta is
            # always ~0 because we are about to overwrite ``latest``.
            previous_event_at = await _previous_note_edited_event_occurred_at(
                db, session_id=session_id, before_id=int(latest.id)
            )
            payload = _build_note_edited_payload(
                body=body,
                now=now,
                previous_event_occurred_at=previous_event_at,
            )
            # In-place coalesce — mutate the existing row's payload but
            # PRESERVE the original occurred_at (burst start). Subscribers
            # will merge by id (same key) on receipt.
            latest.payload = payload
            await db.flush()
            _queue_coalesced_publish(
                db,
                session_id=session_id,
                event_id=int(latest.id),
                payload=payload,
                occurred_at=latest_at,
            )
            return payload

    # Emit branch — no recent event to coalesce into. The previous event
    # (if any) IS ``latest`` here; its occurred_at is the right anchor for
    # seconds_since_last_edit. First-edit case (``latest is None``)
    # naturally yields 0 via the payload builder.
    previous_event_at = (
        _as_utc(latest.occurred_at) if latest is not None else None
    )
    payload = _build_note_edited_payload(
        body=body,
        now=now,
        previous_event_occurred_at=previous_event_at,
    )
    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="note.edited",
        payload=payload,
    )
    return payload


async def _previous_note_edited_event_occurred_at(
    db: AsyncSession, *, session_id: uuid.UUID, before_id: int
) -> datetime | None:
    """Return the ``occurred_at`` of the ``note.edited`` event immediately
    preceding ``before_id`` for ``session_id``, or ``None`` if there is no
    such row (i.e. ``before_id`` is the first ``note.edited`` event).

    Ordering matches :func:`_latest_note_edited_event` (occurred_at desc
    then id desc) so the "previous" is unambiguous even when two events
    were emitted within the same tick.
    """
    stmt = (
        select(SupervisionEvent)
        .where(
            SupervisionEvent.session_id == session_id,
            SupervisionEvent.event_type == "note.edited",
            SupervisionEvent.id < before_id,
        )
        .order_by(SupervisionEvent.occurred_at.desc(), SupervisionEvent.id.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return _as_utc(row.occurred_at)


def _queue_coalesced_publish(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    event_id: int,
    payload: dict[str, Any],
    occurred_at: datetime,
) -> None:
    """Append a publish to the session's pending queue for after-commit drain.

    Mirrors the wire shape :class:`app.sessions.events.EventEmitter`
    uses on the live-emit path (same channel, same JSON keys, same
    ``default=str`` coercion), so the WS subscriber's merge-by-id
    logic doesn't need a coalesce-specific code path. The drain is
    handled by the same ``get_db`` post-commit hook.
    """
    channel = f"events:session:{session_id}"
    try:
        message = json.dumps(
            {
                "id": event_id,
                "session_id": str(session_id),
                "event_type": "note.edited",
                "payload": payload,
                "occurred_at": occurred_at.isoformat(),
            },
            default=str,
        )
    except (TypeError, ValueError, AttributeError) as exc:
        # Symmetric with the live-emit fallback — DB row is already
        # written; dropping the publish only means the WS subscriber
        # catches up on next reconnect.
        logger.warning(
            "notes: coalesced note.edited could not be serialised — "
            "publish dropped (event_id={}): {}",
            event_id,
            exc,
        )
        return
    queue: list[tuple[str, str]] = db.info.setdefault(_PENDING_KEY, [])
    queue.append((channel, message))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/note",
    response_model=SessionNoteRead,
    summary="Fetch the per-session scratchpad note (P1-4)",
)
async def get_note(
    session_id: uuid.UUID,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionNoteRead:
    """Return the current scratchpad body.

    A missing row returns ``body=""`` with ``updated_at`` set to the
    session's start time — we deliberately do NOT insert on GET so a
    fresh session's read stays cheap and free of side effects. The FE
    treats empty == never-written identically.
    """
    session_row = await _require_owned_session(db, session_id, user)

    existing = (
        await db.execute(
            select(SessionNote).where(SessionNote.session_id == session_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        return SessionNoteRead(body="", updated_at=_as_utc(session_row.started_at))
    return SessionNoteRead(body=existing.body, updated_at=_as_utc(existing.updated_at))


@router.put(
    "/{session_id}/note",
    response_model=SessionNoteRead,
    summary="Upsert the per-session scratchpad note (P1-4)",
)
async def put_note(
    session_id: uuid.UUID,
    body: SessionNoteWrite,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionNoteRead:
    """Persist the scratchpad body and emit a coalesced ``note.edited`` event.

    Errors:
      * 403 — caller does not own the session.
      * 409 ``session_not_active`` — session is not active (the
        scratchpad is read-only on terminated/forfeited sessions; the
        body still round-trips via GET so the report page can render
        whatever the user last wrote).
      * 413 ``scratchpad_too_large`` — body exceeds 32 KB UTF-8 bytes.
    """
    request.state.user = user
    session_row = await _require_owned_session(db, session_id, user)
    _require_mutable_session(session_row)

    # Byte-level check (load-bearing): StringConstraints on the schema
    # caps the character count, but a single multi-byte char can blow
    # past the byte budget even when the char count is under cap. The
    # cap is documented in 413's envelope so the FE can render
    # "scratchpad full" without hard-coding the constant.
    body_bytes = body.body.encode("utf-8")
    if len(body_bytes) > NOTE_BODY_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "scratchpad_too_large",
                "message": "scratchpad body exceeds 32 KB limit",
                "limit_bytes": NOTE_BODY_MAX_BYTES,
                "actual_bytes": len(body_bytes),
            },
        )

    now = datetime.now(UTC)
    # Row-level lock on the scratchpad row so concurrent PUTs against
    # the same session serialise their coalesce-or-emit decision. Two
    # racing PUTs that each "saw no recent note.edited event" would
    # otherwise each emit a fresh row instead of one + one coalesce —
    # the supervision_events count would over-report keystroke bursts
    # by exactly the number of overlapping writers. On SQLite the
    # ``.with_for_update()`` clause is a no-op because the database
    # already serialises writes; on Postgres it acquires a FOR UPDATE
    # lock that the second writer blocks on until the first commits.
    await db.execute(
        select(SessionNote)
        .where(SessionNote.session_id == session_id)
        .with_for_update()
    )

    row = await _upsert_note(
        db,
        session_id=session_id,
        body=body.body,
        updated_at=now,
    )

    # The coalesce helper computes seconds_since against the *event*
    # timeline (not the row's updated_at), so it owns the payload
    # construction. It returns the persisted payload so the route
    # handler can include the same values in its debug log.
    payload = await _coalesce_or_emit_note_edited(
        db,
        session_id=session_id,
        body=body.body,
        now=now,
    )

    logger.debug(
        "[notes] session={} bytes={} lines={} seconds_since_last_edit={}",
        session_id,
        payload["bytes"],
        payload["lines"],
        payload["seconds_since_last_edit"],
    )

    return SessionNoteRead(body=row.body, updated_at=_as_utc(row.updated_at))


@router.post(
    "/{session_id}/events/note-viewed",
    status_code=204,
    summary="Record that the user focused the prompt composer while notes had content (P1-4)",
)
async def post_note_viewed(
    session_id: uuid.UUID,
    body: NoteViewedDuringPromptBody,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Emit a ``note.viewed_during_prompt`` supervision event.

    The FE only fires this when the agent-chat composer is focused
    AND the scratchpad pane currently shows a non-empty body. No
    coalescing is applied — the event is naturally rare (one per
    "prompt-with-notes-open" episode) and the timeline reader benefits
    from each occurrence carrying its own ``bytes_at_view`` snapshot.
    """
    request.state.user = user
    session_row = await _require_owned_session(db, session_id, user)
    _require_mutable_session(session_row)

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type="note.viewed_during_prompt",
        payload={"bytes_at_view": int(body.bytes_at_view)},
    )
