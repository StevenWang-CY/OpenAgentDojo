"""Integrity / proctoring signal collection (P0-8).

Receives browser-emitted integrity signals on proctored sessions, persists
them via the supervision-event stream, and increments
``sessions.integrity_signals_count`` so the workspace chip and the
post-mortem walkthrough have a single rolling count to render.

The legal event kinds for this endpoint are a strict subset of the
catalog declared in ``packages/shared-types/src/events.ts`` and
``docs/schemas/event.schema.json``:

* ``tab.blurred``         — payload ``{seconds_visible_before: int}``
* ``tab.focused``         — payload ``{seconds_blurred: int}``
* ``paste.large``         — payload ``{chars: int, target: ...}``
* ``focus.lost``          — payload ``{element_id: str}``
* ``proctored.violation`` — payload ``{kind: str, detail: str}``

Self-study sessions silently accept ``tab.blurred``/``tab.focused`` (the
default FE wiring keeps the visibility listeners attached during the
common honor-mode case so the user can still see "focus" toasts if
clientside instrumentation needs them) but DO NOT persist anything —
the endpoint returns 204 and the row counter does not move. Anything
that isn't one of the five recognised kinds returns 422.

Rate-limit: 60 events / 60s per session (in-memory token bucket keyed
by session_id). The FE debounces to one event per kind per 500ms so a
well-behaved client never approaches this; the bucket is a
defence-in-depth against runaway emitters.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_auth
from app.db.session import get_db
from app.models.session import SessionRow
from app.models.user import User
from app.sessions.events import EventEmitter, get_redis
from app.sessions.router import _require_owned_session

router = APIRouter(prefix="/sessions", tags=["sessions", "integrity"])


# ---------------------------------------------------------------------------
# Bucket
# ---------------------------------------------------------------------------


IntegrityKind = Literal[
    "tab.blurred",
    "tab.focused",
    "paste.large",
    "focus.lost",
    "proctored.violation",
]

_INTEGRITY_KINDS: frozenset[str] = frozenset(
    (
        "tab.blurred",
        "tab.focused",
        "paste.large",
        "focus.lost",
        "proctored.violation",
    )
)

# 60 events per 60 seconds per session. Phase 4.A.8 — primary storage is
# Redis (``auth:integrity_bucket:{session_id}`` counter with a 60s TTL on
# first increment) so a multi-worker deploy shares the gate. The
# in-memory deque is the fallback used only when ``get_redis()`` returns
# ``None`` (laptop dev / tests without Redis); GC kicks in when the
# fallback dict grows past 10k buckets so a long-running process can't
# leak memory off the back of abandoned sessions.
_BUCKET_WINDOW_S: float = 60.0
_BUCKET_LIMIT: int = 60
_REDIS_BUCKET_KEY_PREFIX: str = "auth:integrity_bucket:"
_FALLBACK_BUCKET_GC_THRESHOLD: int = 10_000
_buckets: dict[uuid.UUID, deque[float]] = {}


def _gc_fallback_buckets() -> None:
    """Drop empty deques when ``_buckets`` grows past the GC threshold.

    Walks the dict once and removes entries whose deque is empty or whose
    most-recent timestamp is older than the window. Cheap (O(n)) but
    only runs when the dict is large enough that the cost is paid in
    bulk, not per request.
    """
    if len(_buckets) <= _FALLBACK_BUCKET_GC_THRESHOLD:
        return
    cutoff = time.monotonic() - _BUCKET_WINDOW_S
    drop = [sid for sid, q in _buckets.items() if not q or q[-1] < cutoff]
    for sid in drop:
        _buckets.pop(sid, None)


def _bucket_take_in_memory(session_id: uuid.UUID) -> bool:
    """Original in-memory token bucket — used when Redis is unavailable.

    The deque grows to at most ``_BUCKET_LIMIT`` entries; older
    timestamps fall off the front as the window slides. A miss returns
    False without appending so a flooded session can't keep extending
    the window.
    """
    now = time.monotonic()
    q = _buckets.get(session_id)
    if q is None:
        q = deque()
        _buckets[session_id] = q
    cutoff = now - _BUCKET_WINDOW_S
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= _BUCKET_LIMIT:
        return False
    q.append(now)
    _gc_fallback_buckets()
    return True


async def _bucket_take(session_id: uuid.UUID) -> bool:
    """Consume one slot from ``session_id``'s bucket. Returns True if allowed.

    Phase 4.A.8 — Redis-first. The counter increments via INCR; on the
    first increment (``value == 1``) the key gets a 60-second TTL so
    the bucket auto-resets at the same wall-clock window the in-memory
    implementation used. Falls back to the in-process deque when Redis
    is unavailable (``get_redis`` returns ``None``).
    """
    try:
        redis = await get_redis()
    except Exception:  # pragma: no cover — defensive
        redis = None
    if redis is None:
        return _bucket_take_in_memory(session_id)
    key = _REDIS_BUCKET_KEY_PREFIX + str(session_id)
    try:
        value = await redis.incr(key)
        if value == 1:
            # First write in this window — anchor the TTL. EXPIRE only
            # fires when the key is fresh so a long-running spammer
            # can't keep extending the window with each INCR.
            await redis.expire(key, int(_BUCKET_WINDOW_S))
    except Exception:
        # Redis blip — degrade to the in-memory fallback rather than
        # 500ing the integrity endpoint. The bucket the multi-worker
        # case loses is reseeded on the next successful INCR.
        return _bucket_take_in_memory(session_id)
    if value > _BUCKET_LIMIT:
        return False
    return True


def _reset_bucket(session_id: uuid.UUID) -> None:
    """Drop the bucket for ``session_id`` (test/teardown helper).

    Clears both the in-memory fallback AND the Redis key (best-effort)
    so test isolation is robust regardless of whether Redis is reachable
    in the test environment.
    """
    _buckets.pop(session_id, None)
    try:
        import asyncio

        async def _flush() -> None:
            redis = await get_redis()
            if redis is None:
                return
            try:
                await redis.delete(_REDIS_BUCKET_KEY_PREFIX + str(session_id))
            except Exception:
                pass

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_flush())
        except RuntimeError:
            asyncio.run(_flush())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class IntegrityEventIn(BaseModel):
    """Body of ``POST /sessions/{id}/events/integrity``."""

    kind: IntegrityKind
    # ``payload`` is intentionally an open dict — the shapes are
    # discriminated by ``kind`` per the canonical event catalogue, but the
    # endpoint stores the raw shape so the FE can evolve payload fields
    # without requiring a migration on every browser-side tweak. Per-kind
    # required fields are validated below.
    payload: dict[str, Any] = Field(default_factory=dict)


def _missing_field(field: str) -> HTTPException:
    """Return the canonical 422 envelope for a missing payload field (Phase 4.A.25).

    Centralised so the FE can match on the ``code`` literal without
    having to parse free-form messages — the body shape is
    ``{"code": "missing_payload_field", "field": "..."}``.
    """
    return HTTPException(
        status_code=422,
        detail={
            "code": "missing_payload_field",
            "field": field,
            "message": f"required field '{field}' is missing or null",
        },
    )


def _validate_payload(  # noqa: PLR0912 — branch count is one-per-kind by design
    kind: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Return a cleaned payload for ``kind``, raising 422 on shape errors.

    Phase 4.A.25 — explicit ``missing_payload_field`` 422 envelopes for
    required-but-absent keys (``seconds_visible_before`` on
    ``tab.blurred``, ``seconds_blurred`` on ``tab.focused``, ``chars``
    on ``paste.large``, ``kind`` on ``proctored.violation``). Before
    the fix a missing field silently coerced to 0/empty, which made
    the timeline render a 0-second tab-blur and the score engine
    treat the integrity event as benign.

    The cleanup strips unknown keys (so a stray FE field can't grow
    the row silently) and coerces ints/strings to the documented shapes.
    """
    if kind == "tab.blurred":
        raw = payload.get("seconds_visible_before")
        if raw is None:
            raise _missing_field("seconds_visible_before")
        try:
            seconds = max(0, int(raw))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="seconds_visible_before must be a non-negative integer",
            ) from exc
        return {"seconds_visible_before": seconds}

    if kind == "tab.focused":
        raw = payload.get("seconds_blurred")
        if raw is None:
            raise _missing_field("seconds_blurred")
        try:
            seconds = max(0, int(raw))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="seconds_blurred must be a non-negative integer",
            ) from exc
        return {"seconds_blurred": seconds}

    if kind == "paste.large":
        raw_chars = payload.get("chars")
        if raw_chars is None:
            raise _missing_field("chars")
        try:
            chars = max(0, int(raw_chars))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="chars must be a non-negative integer",
            ) from exc
        target = payload.get("target")
        allowed_targets = {"agent_chat", "editor", "terminal", "other"}
        target_str = str(target) if target is not None else "other"
        if target_str not in allowed_targets:
            target_str = "other"
        return {"chars": chars, "target": target_str}

    if kind == "focus.lost":
        element_id = payload.get("element_id")
        # element_id is best-effort — fall back to empty string so the
        # FE doesn't have to chase a DOM id that some browsers don't expose.
        element_str = str(element_id)[:200] if element_id is not None else ""
        return {"element_id": element_str}

    if kind == "proctored.violation":
        v_kind_raw = payload.get("kind")
        if v_kind_raw is None:
            raise _missing_field("kind")
        allowed_kinds = {
            "right_click",
            "devtools_open",
            "copy_blocked",
            "context_menu",
        }
        v_kind = str(v_kind_raw)
        if v_kind not in allowed_kinds:
            raise HTTPException(
                status_code=422,
                detail=(f"proctored.violation.kind must be one of {sorted(allowed_kinds)}"),
            )
        detail = str(payload.get("detail", ""))[:500]
        return {"kind": v_kind, "detail": detail}

    # Unreachable — the Literal type narrows ``kind`` before we get here.
    raise HTTPException(status_code=422, detail=f"unknown integrity kind: {kind}")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/events/integrity",
    status_code=204,
    summary="Record a proctored-mode integrity signal (P0-8)",
)
async def post_integrity_event(
    session_id: uuid.UUID,
    body: IntegrityEventIn,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Persist an integrity signal for a proctored session.

    Behaviour:
      * Self-study session: drop the event silently, return 204. The FE
        keeps its listeners attached so a future toggle into proctored
        mode (currently rejected at create-time, but defence-in-depth)
        wouldn't surprise the user.
      * Proctored session: rate-limit (60/min/session), persist as a
        supervision event, increment ``integrity_signals_count``,
        return 204.
      * Unknown kind: 422.
      * Bucket full: 429 with ``Retry-After: 60``.

    Ownership is enforced via ``_require_owned_session`` — the same 404
    "session not found" vs. 403 "not your session" envelope every other
    session-scoped endpoint uses.
    """
    request.state.user = user
    row = await _require_owned_session(db, session_id, user)

    if body.kind not in _INTEGRITY_KINDS:
        # The Literal type catches this at request parse time; keep the
        # runtime guard so a typed bypass (e.g. tests posting raw dicts)
        # still fails cleanly.
        raise HTTPException(status_code=422, detail=f"unknown integrity kind: {body.kind}")

    # Self-study sessions: drop silently. This is intentionally NOT a 409 —
    # the FE may transiently emit a signal during the create→provision
    # window before it knows the session's mode, and we want that path to
    # be a no-op rather than a toast-inducing error.
    if row.mode != "proctored":
        return Response(status_code=204)

    # Rate-limit gate. Awaited because Phase 4.A.8 moved the bucket to
    # Redis (with an in-memory fallback when Redis is down).
    if not await _bucket_take(session_id):
        logger.warning(
            "[integrity] rate limit hit for session={} kind={}",
            session_id,
            body.kind,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "integrity_rate_limited",
                "message": "too many integrity signals in the last minute",
                "limit": _BUCKET_LIMIT,
                "window_seconds": int(_BUCKET_WINDOW_S),
            },
            headers={"Retry-After": str(int(_BUCKET_WINDOW_S))},
        )

    cleaned = _validate_payload(body.kind, body.payload)

    redis = await get_redis()
    emitter = EventEmitter(db=db, redis_client=redis)
    await emitter.emit(
        session_id=session_id,
        event_type=body.kind,
        payload=cleaned,
    )

    # Atomic increment so two concurrent integrity posts can't lose an
    # update via a stale ORM instance. The +1 lands inside the same unit
    # of work as the supervision-event insert so a rollback (e.g. Redis
    # publish failure that escalates) keeps the counter honest.
    await db.execute(
        update(SessionRow)
        .where(SessionRow.id == session_id)
        .values(integrity_signals_count=SessionRow.integrity_signals_count + 1)
    )
    await db.flush()
    return Response(status_code=204)


__all__ = ["_INTEGRITY_KINDS", "_reset_bucket", "router"]
