"""Authentication routes: magic-link flow, logout, and /me.

Endpoints
---------
POST /auth/magic-link    — request a magic link email
GET  /auth/callback      — exchange token → session cookie + redirect
POST /auth/logout        — clear session cookie
GET  /auth/me            — return current user + fresh CSRF token
POST /auth/csrf-refresh  — force-rotate the CSRF cookie

The top-level ``/me`` alias was retired (see main.py) because the FE only
calls ``/auth/me`` and the duplicate path doubled the OpenAPI surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from loguru import logger
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import _COOKIE_MAX_AGE, _CSRF_COOKIE_NAME, issue_csrf_token
from app.auth.deps import require_auth
from app.auth.email import (
    send_deletion_scheduled_email,
    send_email_change_link,
    send_magic_link_email,
)
from app.auth.hashing import hash_email_for_event
from app.auth.magic_link import (
    consume_email_change_token,
    consume_magic_token,
    create_email_change_link,
    create_magic_link,
)
from app.auth.session_cookie import (
    mint_session_cookie_for_user,
    revoke_session_cookie,
    rotate_user_session_epoch,
)
from app.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.models.user_consent import AccountEvent, UserConsent
from app.schemas.consent import ConsentRecord, ConsentState, ConsentUpdate
from app.schemas.user import (
    DataExportRead,
    DeleteAccountRequest,
    DeletionLockError,
    DeletionScheduledRead,
    DisplayNameUpdate,
    EmailChangeConfirm,
    EmailChangeRequest,
    UserRead,
)

# Shared OpenAPI 403 declaration for every mutating P0-6 endpoint the
# DeletionLockMiddleware can intercept. Spelled out once so adding a new
# mutating route is a one-liner instead of a copy-paste hazard.
_DELETION_LOCK_RESPONSE = {
    403: {
        "model": DeletionLockError,
        "description": (
            "Account scheduled for deletion. ``code='deletion_scheduled'`` "
            "with ``scheduled_for`` carrying the ISO-8601 grace-end timestamp. "
            "Cancel via ``POST /auth/me/delete/cancel`` (the only exempt "
            "mutating endpoint)."
        ),
    },
}

router = APIRouter(prefix="/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    email: EmailStr


# ---------------------------------------------------------------------------
# POST /auth/magic-link
# ---------------------------------------------------------------------------


@router.post("/magic-link", status_code=204, summary="Request a magic login link")
async def post_magic_link(
    body: MagicLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Issue a magic-link email.

    Always returns 204 — we deliberately do NOT distinguish between "user
    exists / email sent" and "delivery failed" so we can't be used as an
    account-existence oracle. Internal observability still records the
    failure via :func:`loguru.warning`.
    """
    settings = get_settings()
    # The link in the email points at the *web* frontend's /auth/callback,
    # which forwards to the API's /api/v1/auth/callback. Using request.base_url
    # (= the API host) lands users on a 404 because /auth/callback is a
    # Next.js route, not a backend one. Prefer settings.web_origin when set;
    # fall back to request.base_url only when no web origin is configured.
    base_url = (settings.web_origin or str(request.base_url)).rstrip("/")
    magic_url = await create_magic_link(db, email=str(body.email), base_url=base_url)

    # Commit the magic-link row BEFORE we await on outbound email — otherwise
    # a slow/wedged SMTP backend pins this DB connection (and its row-level
    # locks) for the duration of the send timeout. The link is persistent in
    # the DB the moment we commit, so the user can retry email delivery
    # without losing the token.
    await db.commit()

    from loguru import logger as _logger

    # ``create_magic_link`` returns ``None`` when the requested address is
    # currently reserved as ``pending_email`` on another account (P0-6
    # reverse-direction TOCTOU defence). In that window the endpoint MUST
    # still return 204 — the standard privacy-preserving convention — but
    # there is no token to deliver. The skip is already logged at info-
    # level by ``create_magic_link``; we exit early to keep the email
    # backend out of the picture entirely.
    if magic_url is None:
        return Response(status_code=204)

    try:
        delivered = await asyncio.wait_for(
            send_magic_link_email(
                to_email=str(body.email),
                magic_url=magic_url,
                settings=settings,
            ),
            timeout=10.0,
        )
    except TimeoutError:
        # The token is already persisted; treat email-send timeout as a
        # warning, not a request failure. The user can request a fresh link.
        # Hashed email keeps the audit trail useful without leaking PII
        # (the redaction filter would mask the raw value anyway, but we
        # prefer to never even render it in the first place).
        _logger.warning(
            "[auth] magic-link email send timed out email_hash={} (token already persisted)",
            hash_email_for_event(str(body.email), settings),
        )
        delivered = False

    if not delivered:
        _logger.error(
            "[auth] magic-link delivery FAILED email_hash={} (no backend confirmed delivery)",
            hash_email_for_event(str(body.email), settings),
        )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /auth/callback
# ---------------------------------------------------------------------------


@router.get("/callback", summary="Exchange magic-link token for a session")
async def get_callback(
    request: Request,
    token: str = Query(..., description="Raw magic-link token from the email URL"),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    user = await consume_magic_token(db, token)
    if user is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_magic_link",
                "message": "invalid or expired magic link",
            },
        )

    # Redirect to the frontend /missions page (absolute URL so the browser
    # goes to the web origin, not the API origin).
    frontend_url = f"{settings.web_origin.rstrip('/')}/missions"
    redirect = RedirectResponse(url=frontend_url, status_code=302)
    # Stamp the user's current session_epoch into the cookie so a future
    # "sign out everywhere" can invalidate this login alongside any other
    # live cookies. mint_session_cookie_for_user pulls epoch off the row.
    mint_session_cookie_for_user(redirect, user, settings)
    issue_csrf_token(redirect, settings)
    logger.info(
        "auth.callback.success user_id={} ip={}",
        user.id,
        request.client.host if request.client else "unknown",
    )
    return redirect


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=204, summary="Clear the session cookie")
async def post_logout(request: Request) -> Response:
    """Clear the session cookie and revoke its JTI so the token cannot replay."""
    settings = get_settings()
    response = Response(status_code=204)
    revoke_session_cookie(response, settings, request=request)
    # Also drop the CSRF cookie so a second user on the same browser does
    # not inherit the first user's CSRF token (the cookie max-age otherwise
    # outlives the session and is non-HttpOnly).
    response.delete_cookie(
        key=_CSRF_COOKIE_NAME,
        path="/",
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    user_id: str | None = None
    raw = request.cookies.get(settings.session_cookie_name)
    if raw:
        try:
            from jose import jwt as _jwt

            payload = _jwt.decode(raw, settings.session_secret, algorithms=["HS256"])
            user_id = payload.get("sub")
        except Exception:  # pragma: no cover — best-effort logging
            pass
    logger.info(
        "auth.logout user_id={} ip={}",
        user_id or "unknown",
        request.client.host if request.client else "unknown",
    )
    return response


# ---------------------------------------------------------------------------
# GET /me — shared helper + two routers (auth-scoped and top-level alias)
# ---------------------------------------------------------------------------


def _build_me_response(user: User, request: Request) -> JSONResponse:
    """Build the /me JSONResponse with CSRF token.

    P1-B27: ``/me`` is a *read* endpoint and used to mint a fresh CSRF token
    on every poll, which silently invalidated whatever the FE already had
    cached. We now reuse the existing cookie when one is present so the FE
    can keep working without re-fetching the body token.

    We keep the manual ``JSONResponse`` construction (rather than returning
    the ``UserRead`` model directly) because we still need to ``set_cookie``
    when minting a fresh token — letting FastAPI serialize would discard
    the cookie mutation.
    """
    settings = get_settings()
    existing_csrf = request.cookies.get(_CSRF_COOKIE_NAME, "")
    csrf_value = existing_csrf or secrets.token_hex(32)
    # Build the body via the schema directly so the wire format always matches
    # the OpenAPI contract (csrf_token: str — required since P0).
    payload = UserRead.model_validate(
        {
            "id": user.id,
            "email": user.email,
            "handle": user.handle,
            "display_name": user.display_name,
            "github_login": user.github_login,
            "created_at": user.created_at,
            "last_login_at": user.last_login_at,
            "csrf_token": csrf_value,
            "tutorial_completed_at": user.tutorial_completed_at,
            "tutorial_replay_count": user.tutorial_replay_count,
            # P0-6 — surfaces the in-flight email change banner + the
            # 7-day deletion countdown on the FE /account page.
            "pending_email": user.pending_email,
            "deletion_scheduled_at": user.deletion_scheduled_at,
        }
    )

    response = JSONResponse(content=payload.model_dump(mode="json"))
    if not existing_csrf:
        response.set_cookie(
            key=_CSRF_COOKIE_NAME,
            value=csrf_value,
            httponly=False,
            secure=settings.cookie_secure,
            samesite="lax",
            max_age=_COOKIE_MAX_AGE,
            path="/",
        )
    return response


@router.get("/me", response_model=UserRead, summary="Return the authenticated user")
async def get_me(
    request: Request,
    user: User = Depends(require_auth),
) -> JSONResponse:
    return _build_me_response(user, request)


@router.post(
    "/csrf-refresh",
    summary="Force-rotate the CSRF token (clears the existing cookie)",
)
async def post_csrf_refresh(
    request: Request,
    user: User = Depends(require_auth),
) -> JSONResponse:
    """Explicit endpoint to rotate the CSRF cookie.

    Useful for tooling / Selenium that wants a fresh token without going
    through ``/auth/callback``.
    """
    # Clear the existing cookie so _build_me_response mints a new value.
    request.cookies.pop(_CSRF_COOKIE_NAME, None)
    return _build_me_response(user, request)


# ---------------------------------------------------------------------------
# POST /auth/me/tutorial/replay  (P0-1)
# ---------------------------------------------------------------------------


@router.post(
    "/me/tutorial/replay",
    response_model=UserRead,
    summary="Clear tutorial completion + increment replay count (P0-1)",
)
async def post_tutorial_replay(
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Re-arm the tutorial coachmark for the signed-in user.

    Atomic: a single SQL ``UPDATE`` clears ``tutorial_completed_at`` and
    increments ``tutorial_replay_count`` server-side. The previous
    Python-side read-modify-write lost increments under concurrent
    replays (two callers each read N, both wrote N+1).
    """
    from sqlalchemy import update as sa_update

    await db.execute(
        sa_update(User)
        .where(User.id == user.id)
        .values(
            tutorial_completed_at=None,
            tutorial_replay_count=User.tutorial_replay_count + 1,
        )
    )
    await db.flush()
    # The outer ``get_db`` dependency commits at request boundary, but
    # the response is built BEFORE that commit — refresh the in-memory
    # ORM instance so the caller sees the freshly-incremented counter.
    await db.refresh(user)
    return _build_me_response(user, request)


# ---------------------------------------------------------------------------
# GET /auth/me/consent  +  POST /auth/me/consent  (P0-5)
# ---------------------------------------------------------------------------
#
# Mounted on ``auth_router`` (effective path ``/api/v1/auth/me/consent``)
# rather than a fresh top-level ``me_router`` for the same reason the
# tutorial-replay endpoint lives here: the prior top-level alias confused
# the OpenAPI → TS generator with duplicate operation IDs.


_USER_AGENT_MAX_CHARS = 512


def _hash_remote_ip(request: Request) -> str | None:
    """SHA-256 the (settings.ip_hash_salt + remote_addr) for the consent row.

    Returns ``None`` when no client IP is available (test client without a
    transport-level peer) so the column stays honest about what we know.
    """
    settings = get_settings()
    client = request.client
    if client is None or not client.host:
        return None
    salt = (settings.ip_hash_salt or "").encode("utf-8")
    return hashlib.sha256(salt + client.host.encode("utf-8")).hexdigest()


def _truncated_user_agent(request: Request) -> str | None:
    """Return the request UA capped at 512 chars (or None when absent)."""
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return ua[:_USER_AGENT_MAX_CHARS]


@router.get(
    "/me/consent",
    response_model=ConsentState,
    summary="Return the caller's most-recent consent decision per kind (P0-5)",
)
async def get_me_consent(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ConsentState:
    """Return the latest ``UserConsent`` per kind for the authenticated user.

    Each of the three kinds (``analytics``, ``functional``, ``marketing``)
    surfaces either the most-recent decision (by ``granted_at``) or
    ``None`` when no row exists. The endpoint is read-only and side-effect
    free; the FE polls it on app boot to decide whether the cookie banner
    needs to render.
    """
    rows = (
        await db.execute(
            select(UserConsent)
            .where(UserConsent.user_id == user.id)
            .order_by(UserConsent.granted_at.desc())
        )
    ).scalars().all()

    # First-seen-wins under the ``ORDER BY granted_at DESC`` projection
    # yields the latest record per kind in a single pass; no need for a
    # window function over a tiny table indexed on (user_id, kind, granted_at).
    latest: dict[str, UserConsent] = {}
    for row in rows:
        if row.kind not in latest:
            latest[row.kind] = row

    def _project(row: UserConsent | None) -> ConsentRecord | None:
        if row is None:
            return None
        return ConsentRecord(granted=row.granted, version=row.version, at=row.granted_at)

    return ConsentState(
        analytics=_project(latest.get("analytics")),
        functional=_project(latest.get("functional")),
        marketing=_project(latest.get("marketing")),
    )


@router.post(
    "/me/consent",
    status_code=204,
    summary="Record a consent decision (append-only audit trail) (P0-5)",
)
async def post_me_consent(
    body: ConsentUpdate,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Insert a new ``UserConsent`` row + emit a ``consent.*`` event.

    Append-only by design: replaying a POST writes a new row rather than
    mutating an existing one, so the FULL consent history is recoverable
    for a regulator. The server stamps ``version`` from
    ``settings.consent_policy_version`` — the client cannot influence the
    recorded policy version.

    The supervision-style event lands on the dedicated ``account_events``
    table (the platform's main ``supervision_events`` table requires a
    NOT NULL session FK, and consent is account-scoped, not session-scoped).
    The same table now also carries the P0-6 ``account.*`` events — see
    :class:`AccountEvent` for the full event-type allow-list.
    """
    # Lazy import to avoid a circular at module load (observability imports
    # config, which imports nothing here, but keeping the lazy form matches
    # the pattern other handlers use for prometheus counters).
    from app.observability import consent_recorded_total

    settings = get_settings()
    policy_version = settings.consent_policy_version

    consent = UserConsent(
        user_id=user.id,
        kind=body.kind,
        granted=body.granted,
        version=policy_version,
        ip_address_hash=_hash_remote_ip(request),
        user_agent=_truncated_user_agent(request),
    )
    db.add(consent)

    event_type_literal = "consent.granted" if body.granted else "consent.revoked"
    db.add(
        AccountEvent(
            user_id=user.id,
            event_type=event_type_literal,
            payload={"kind": body.kind, "version": policy_version},
        )
    )

    await db.flush()

    consent_recorded_total.labels(
        kind=body.kind,
        granted="true" if body.granted else "false",
    ).inc()

    logger.info(
        "auth.consent.recorded user_id={} kind={} granted={} version={}",
        user.id,
        body.kind,
        body.granted,
        policy_version,
    )
    return Response(status_code=204)


# The top-level ``me_router`` (which mounted ``/api/v1/me`` as an alias for
# ``/api/v1/auth/me``) was removed: the FE only ever hits ``/auth/me`` and
# shipping two paths for one handler confused the OpenAPI → TS generator
# (it minted two equivalent operation IDs). Re-add via main.py if a new
# client ever needs the top-level shape.


# ===========================================================================
# P0-6 — Account self-service
# ===========================================================================
#
# Eight endpoints under /api/v1/auth/me/* implementing profile editing,
# the two-step email-change flow, "sign out everywhere", per-user data
# export (kicks an RQ job; polls for status; serves a signed download
# URL), and the 7-day account-deletion grace timer. The lockout side of
# the deletion grace is enforced by :class:`DeletionLockMiddleware`.
#
# Every event the user can take here emits a typed account event on the
# :class:`AccountEvent` table (the platform's account-scoped log; we use
# the same table P0-5 introduced because the main supervision_events
# table's ``session_id`` is NOT NULL and these are account-scoped, not
# session-scoped). The event names use the ``account.*`` namespace per
# P0_DESIGN §0.3.


# ---------------------------------------------------------------------------
# Account event store — design note
# ---------------------------------------------------------------------------
#
# Migration 0017 promoted the P0-5 ``consent_events`` table to
# ``account_events`` and widened the CHECK to cover the P0-6 ``account.*``
# literals alongside the existing ``consent.*`` ones (see
# :data:`app.models.user_consent.ALLOWED_ACCOUNT_EVENT_TYPES`). Helpers
# below stage an :class:`AccountEvent` row INSIDE the caller's
# transaction so a route that fails mid-state-change does NOT leave a
# dangling event behind (the row rolls back with everything else). The
# replay tool unions ``account_events`` with ``supervision_events`` for
# the cross-stream view.


def _build_account_event(
    user_id, event_type: str, payload: dict
) -> AccountEvent:
    """Build an :class:`AccountEvent` ORM object for the caller to ``db.add``.

    Kept as a pure factory (no ``db`` parameter, no I/O) so callers can
    stage the row inside their existing transaction and roll it back
    naturally if a downstream step fails. The caller is responsible for
    ``db.add(event)`` BEFORE the commit that lands the state change.
    """
    return AccountEvent(
        user_id=user_id,
        event_type=event_type,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# PATCH /auth/me
# ---------------------------------------------------------------------------


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the caller's profile (P0-6 — display_name only)",
    responses=_DELETION_LOCK_RESPONSE,
)
async def patch_me(
    body: DisplayNameUpdate,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update the caller's mutable profile fields.

    Currently the only mutable field is ``display_name`` (handle changes
    are not supported in MVP per P0_DESIGN §P0-6 "Open decisions").
    The schema's ``extra='forbid'`` makes a client that POSTs ``handle``
    fail at validation with a 422 + a typed FE error.
    """
    if body.display_name is not None:
        user.display_name = body.display_name
        db.add(user)
        await db.flush()
        await db.refresh(user)
    logger.info("auth.profile_updated user_id={}", user.id)
    return _build_me_response(user, request)


# ---------------------------------------------------------------------------
# POST /auth/me/email/change
# ---------------------------------------------------------------------------


def _hash_email_for_event(settings, email: str) -> str:
    """Thin route-local wrapper around :func:`app.auth.hashing.hash_email_for_event`.

    Retained as a module-level alias because external tooling and tests
    import it from this module directly. The actual implementation moved
    to ``app.auth.hashing`` so other modules (notably ``auth/email.py``
    and ``auth/magic_link.py``) can share the same salt + algorithm
    without importing from a route module (which would create cycles).
    """
    return hash_email_for_event(email, settings)


@router.post(
    "/me/email/change",
    status_code=204,
    summary="Start the two-step email-change flow (P0-6)",
    responses=_DELETION_LOCK_RESPONSE,
)
async def post_me_email_change(
    body: EmailChangeRequest,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Set ``users.pending_email`` and email a magic link to the NEW address.

    Validation:

    * New email must be syntactically valid (Pydantic ``EmailStr``).
    * New email must not equal an existing ``users.email`` or
      ``users.pending_email`` of any account (409 on collision).
    * The caller's account must not be scheduled for deletion (the
      :class:`DeletionLockMiddleware` enforces this — by the time this
      handler runs, ``user.deletion_scheduled_at`` is guaranteed NULL).
    """
    from app.observability import email_change_requested_total

    new_email = str(body.new_email).strip().lower()
    if new_email == (user.email or "").lower():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "email_unchanged",
                "message": "new_email matches the current email",
            },
        )

    # Conflict check spans both ``email`` and ``pending_email`` so a race
    # to claim the same target gets rejected on the second comer. The check
    # uses case-insensitive comparison via CITEXT on Postgres and explicit
    # lower() on SQLite (the column is mapped as TEXT under the test
    # harness; see conftest._patch_models_for_sqlite).
    from sqlalchemy import func as sa_func
    from sqlalchemy import or_ as sa_or

    collision = (
        await db.execute(
            select(User).where(
                User.id != user.id,
                sa_or(
                    sa_func.lower(User.email) == new_email,
                    sa_func.lower(User.pending_email) == new_email,
                ),
            )
        )
    ).scalar_one_or_none()
    if collision is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "email_in_use",
                "message": "Another account is already using or claiming this email",
            },
        )

    user.pending_email = new_email
    db.add(user)
    await db.flush()

    settings = get_settings()
    base_url = (settings.web_origin or str(request.base_url)).rstrip("/")
    magic_url = await create_email_change_link(
        db,
        user=user,
        new_email=new_email,
        base_url=base_url,
    )
    # Stage the account-event row INSIDE the same transaction as the
    # pending_email + token writes so a downstream rollback (e.g. a flush
    # error) drops the event with everything else. The event is durable
    # the moment the commit below lands.
    db.add(
        _build_account_event(
            user.id,
            "account.email_change_requested",
            {"new_email_hash": _hash_email_for_event(settings, new_email)},
        )
    )
    await db.commit()

    # Dispatch the email after commit so a slow SMTP backend never holds
    # a transaction open. Mirrors the magic-link flow.
    try:
        await asyncio.wait_for(
            send_email_change_link(
                to_email=new_email,
                magic_url=magic_url,
                settings=settings,
            ),
            timeout=10.0,
        )
    except TimeoutError:
        logger.warning("email-change email send timed out (token persisted)")

    email_change_requested_total.inc()
    logger.info(
        "auth.email_change_requested user_id={} new_email_hash={}",
        user.id,
        _hash_email_for_event(settings, new_email),
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# POST /auth/me/email/confirm
# ---------------------------------------------------------------------------


@router.post(
    "/me/email/confirm",
    response_model=UserRead,
    summary="Confirm an in-flight email change (P0-6)",
    responses={
        # 409 has two distinct envelopes the FE differentiates by ``code``:
        #   * ``no_pending_email`` — the user already confirmed (or
        #     cancelled) and there's nothing to land.
        #   * ``email_taken_in_flight`` — another account claimed the
        #     target address between change-request and confirm; the
        #     user must restart the flow with a different address.
        409: {
            "description": (
                "The email change can't be confirmed — either there is no "
                "pending change (``no_pending_email``) or the address was "
                "claimed by another account before confirm "
                "(``email_taken_in_flight``)."
            ),
        },
        **_DELETION_LOCK_RESPONSE,
    },
)
async def post_me_email_confirm(
    body: EmailChangeConfirm,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Consume the email-change token and land the new email atomically.

    Server-side checks:

    * Token must be valid + unused + not expired (consume_email_change_token
      handles all three).
    * Token's ``user_id`` must equal the calling user's id.
    * ``user.pending_email`` must still be set (cleared by another path
      would mean the user already confirmed via a different device).

    After the email lands we rotate the session epoch — every cookie minted
    before this moment is now invalid except the one we mint right here.
    """
    from app.observability import email_change_confirmed_total

    token_row = await consume_email_change_token(db, body.token)
    if token_row is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_token", "message": "invalid or expired token"},
        )
    if token_row.user_id != user.id:
        # Cross-account token presented while signed in as someone else —
        # treat as invalid (do NOT 403, which would confirm token validity).
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_token", "message": "invalid or expired token"},
        )
    if not user.pending_email:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_pending_email",
                "message": "no email change is in flight for this account",
            },
        )

    # Capture the user's identity as a plain Python value BEFORE we mutate
    # the ORM object — we need it on the recovery path after a rollback
    # detaches the instance and lazy attribute access would otherwise
    # raise ``DetachedInstanceError`` / ``MissingGreenlet``.
    user_id = user.id
    # Land the change + rotate the epoch atomically.
    new_email = user.pending_email
    user.email = new_email
    user.pending_email = None
    rotate_user_session_epoch(user)
    db.add(user)
    # Event row joins the same transaction as the email/epoch updates so
    # the audit log can never get ahead of the actual state change.
    db.add(_build_account_event(user_id, "account.email_changed", {}))
    try:
        await db.flush()
    except IntegrityError:
        # Reverse-direction TOCTOU (P0-6 audit). Between this user setting
        # ``pending_email`` and clicking the confirm link, some OTHER
        # account managed to register an account on the SAME address —
        # typically via a magic-link sign-up that slipped past the
        # ``_pending_email_owner`` guard in a tight window (e.g. the user
        # cleared & re-set pending_email between the guard check and the
        # other party's user-row insert). The UNIQUE constraint on
        # ``users.email`` raises here; we must roll back, drop the
        # caller's now-impossible pending_email so the FE can prompt them
        # to retry with a different address, and surface a typed 409 so
        # the FE doesn't render the generic 500 page. Failure-path
        # observability lands via ``logger.warning`` per the design
        # accommodation (no new migration for an event-type literal).
        await db.rollback()
        # Re-fetch the user into the rolled-back session via the captured
        # ``user_id`` (the in-memory ORM object is now detached and any
        # attribute access on it would trigger a lazy load against a dead
        # session). Clear the impossible ``pending_email`` so the FE can
        # prompt the caller to retry with a different address.
        #
        # Critically, we ``commit()`` the cleanup explicitly: ``get_db``
        # rolls the request's session back on any exception, including the
        # HTTPException we're about to raise — which would wipe this
        # compensating change. The subsequent rollback by ``get_db`` runs
        # against an empty transaction and is a harmless no-op.
        refetched = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if refetched is not None and refetched.pending_email is not None:
            refetched.pending_email = None
            db.add(refetched)
            await db.commit()
        logger.warning(
            "auth.email_change_failed user_id={} reason=email_taken_in_flight",
            user_id,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "email_taken_in_flight",
                "message": (
                    "This email was claimed by another account before you "
                    "could confirm."
                ),
            },
        ) from None
    await db.refresh(user)

    email_change_confirmed_total.inc()
    logger.info("auth.email_changed user_id={}", user.id)

    response = _build_me_response(user, request)
    # Mint a fresh cookie for the caller — this is the ONLY cookie that
    # survives the epoch rotation. Every other device is now logged out.
    mint_session_cookie_for_user(response, user, get_settings())
    return response


# ---------------------------------------------------------------------------
# POST /auth/me/sessions/sign-out-all
# ---------------------------------------------------------------------------


@router.post(
    "/me/sessions/sign-out-all",
    status_code=204,
    summary="Invalidate every other live session for the caller (P0-6)",
    responses=_DELETION_LOCK_RESPONSE,
)
async def post_me_sign_out_all(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Bump the user's session_epoch and mint a fresh cookie for the caller.

    Other devices' cookies will fail verification on their next request.
    The current caller keeps their session via the fresh cookie attached
    to the response.
    """
    from app.observability import account_sign_out_all_total

    rotate_user_session_epoch(user)
    db.add(user)
    # Persist the event in the same transaction as the epoch bump so a
    # crash between epoch-rotate and event-write cannot land one without
    # the other.
    db.add(_build_account_event(user.id, "account.signed_out_all_sessions", {}))
    await db.flush()
    await db.refresh(user)

    account_sign_out_all_total.inc()
    logger.info("auth.signed_out_all_sessions user_id={}", user.id)

    response = Response(status_code=204)
    mint_session_cookie_for_user(response, user, get_settings())
    return response


# ---------------------------------------------------------------------------
# POST /auth/me/data-export
# ---------------------------------------------------------------------------


@router.post(
    "/me/data-export",
    response_model=DataExportRead,
    status_code=202,
    summary="Kick off an asynchronous account data-export job (P0-6)",
    responses=_DELETION_LOCK_RESPONSE,
)
async def post_me_data_export(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> DataExportRead:
    """Insert a ``queued`` row and enqueue the export worker.

    Returns 409 if there is already a ``queued`` or ``running`` export for
    this user — the partial unique index enforces this at the DB layer on
    Postgres, but we also check at the application layer for SQLite (which
    ignores ``WHERE`` on unique indexes) AND as defence-in-depth.

    If the RQ queue is unreachable we fall back to running the worker
    inline in the request loop — same pattern as ``provision_in_process``.
    """
    from app.models.data_export import (
        EXPORT_IN_FLIGHT_STATUSES,
        EXPORT_STATUS_QUEUED,
        DataExport,
    )
    from app.observability import data_exports_requested_total
    from app.workers.account_export import build_user_export
    from app.workers.queue import get_queue

    existing = (
        await db.execute(
            select(DataExport).where(
                DataExport.user_id == user.id,
                DataExport.status.in_(list(EXPORT_IN_FLIGHT_STATUSES)),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "export_in_flight",
                "message": "an export is already running for this account",
                "export_id": str(existing.id),
            },
        )

    export = DataExport(user_id=user.id, status=EXPORT_STATUS_QUEUED)
    db.add(export)
    await db.flush()
    # Commit before enqueue so the worker can find the row.
    await db.commit()
    data_exports_requested_total.labels(status=EXPORT_STATUS_QUEUED).inc()
    logger.info(
        "auth.data_export_requested user_id={} export_id={}",
        user.id,
        export.id,
    )

    queue = get_queue()
    if queue is not None:
        try:
            queue.enqueue("app.workers.account_export.build_user_export", str(export.id))
        except Exception as exc:
            logger.warning(
                "data-export enqueue failed for {}, running inline: {}", export.id, exc
            )
            queue = None

    if queue is None:
        # Inline fallback — runs the worker in the request loop so dev /
        # test environments without Redis still produce a real export.
        # ``build_user_export`` is sync (it wraps an asyncio.run) so we
        # schedule it on a thread to avoid nesting event loops.
        #
        # ``inline=True`` tells the worker to suppress the post-mark
        # re-raise: the row already carries the failure state (status,
        # error), and the route is about to serialise the row + return
        # 202. A raise would 500 the request and force the FE to render
        # a generic error page even though the per-export state surfaces
        # the failure cleanly via subsequent GETs.
        try:
            await asyncio.to_thread(build_user_export, str(export.id), inline=True)
        except Exception as exc:
            # Defensive — should be unreachable because inline=True
            # suppresses re-raises inside the worker. If something raises
            # *outside* that flow (e.g. asyncio.to_thread itself), log
            # loudly and refresh the row so the response still serialises
            # whatever state the worker managed to land.
            logger.warning(
                "data-export inline worker raised despite inline=True for {}: {}",
                export.id,
                exc,
            )
        # Refresh the row so the response shows the terminal state.
        await db.refresh(export)

    return DataExportRead.model_validate(export)


# ---------------------------------------------------------------------------
# GET /auth/me/data-export/{export_id}
# ---------------------------------------------------------------------------


@router.get(
    "/me/data-export/{export_id}",
    response_model=DataExportRead,
    summary="Poll a data-export job and (if ready) get a signed download URL (P0-6)",
)
async def get_me_data_export(
    export_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> DataExportRead:
    import uuid as _uuid
    from datetime import UTC, datetime

    from app.models.data_export import (
        EXPORT_STATUS_EXPIRED,
        EXPORT_STATUS_READY,
        DataExport,
    )

    try:
        export_uuid = _uuid.UUID(export_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="export not found") from exc

    export = (
        await db.execute(
            select(DataExport).where(
                DataExport.id == export_uuid,
                DataExport.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")

    now = datetime.now(UTC)
    download_url: str | None = None

    if export.status == EXPORT_STATUS_READY:
        # Normalise expires_at to UTC-aware so SQLite naive timestamps
        # don't trip the comparison.
        expires_at = export.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at is not None and expires_at <= now:
            # Lazy expiry — flip the row so the next caller sees the right
            # state without waiting for the (future) sweeper.
            export.status = EXPORT_STATUS_EXPIRED
            db.add(export)
            await db.flush()
        elif export.s3_key and expires_at is not None:
            remaining = max(1, int((expires_at - now).total_seconds()))
            from app.storage import generate_download_url

            download_url = generate_download_url(export.s3_key, expires_in=remaining)

    response = DataExportRead.model_validate(export)
    if download_url is not None:
        response = response.model_copy(update={"download_url": download_url})
    return response


# ---------------------------------------------------------------------------
# POST /auth/me/delete
# ---------------------------------------------------------------------------


@router.post(
    "/me/delete",
    response_model=DeletionScheduledRead,
    summary="Schedule the caller's account for hard deletion in 7 days (P0-6)",
    responses=_DELETION_LOCK_RESPONSE,
)
async def post_me_delete(
    body: DeleteAccountRequest,
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    from datetime import UTC, datetime, timedelta

    from app.observability import account_deletions_scheduled_total

    settings = get_settings()
    if str(body.confirm_email).strip().lower() != (user.email or "").lower():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "email_mismatch",
                "message": "confirm_email must equal the account's current email",
            },
        )

    now = datetime.now(UTC)
    scheduled_for = now + timedelta(days=settings.account_deletion_grace_days)
    user.deletion_scheduled_at = scheduled_for
    # Rotate epoch so any OTHER live device is logged out — but mint a
    # fresh cookie for the caller below so they can still navigate to
    # /account and cancel without being kicked out themselves.
    rotate_user_session_epoch(user)
    db.add(user)
    # The scheduled-deletion event MUST persist with the schedule itself
    # (so the audit log can never claim a deletion was scheduled when the
    # row write rolled back, or vice versa).
    db.add(
        _build_account_event(
            user.id,
            "account.deletion_scheduled",
            {"scheduled_for": scheduled_for.isoformat()},
        )
    )
    await db.flush()
    await db.refresh(user)

    account_deletions_scheduled_total.inc()
    logger.info(
        "auth.deletion_scheduled user_id={} scheduled_for={}",
        user.id,
        scheduled_for.isoformat(),
    )

    base_url = (settings.web_origin or str(request.base_url)).rstrip("/")
    cancel_url = f"{base_url}/account"
    try:
        await asyncio.wait_for(
            send_deletion_scheduled_email(
                to_email=user.email,
                cancel_url=cancel_url,
                scheduled_for_iso=scheduled_for.isoformat(),
                settings=settings,
            ),
            timeout=10.0,
        )
    except TimeoutError:
        logger.warning("deletion-scheduled email send timed out")

    body_obj = DeletionScheduledRead(scheduled_for=scheduled_for)
    response = JSONResponse(content=body_obj.model_dump(mode="json"))
    mint_session_cookie_for_user(response, user, settings)
    return response


# ---------------------------------------------------------------------------
# POST /auth/me/delete/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/me/delete/cancel",
    status_code=204,
    summary="Cancel a pending account deletion during the grace window (P0-6)",
)
async def post_me_delete_cancel(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    from datetime import UTC, datetime

    from app.observability import account_deletions_cancelled_total

    if user.deletion_scheduled_at is None:
        # Idempotent no-op — there's nothing to cancel.
        return Response(status_code=204)
    # SQLite returns naive datetimes; Postgres returns aware. Normalise so
    # the comparison never raises ``can't compare offset-naive and
    # offset-aware datetimes`` on the test harness.
    scheduled = user.deletion_scheduled_at
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    if scheduled <= datetime.now(UTC):
        # Grace already expired; the worker may have started. Return 410
        # so the FE can show "deletion already processed" instead of a
        # stale "your cancel went through" toast.
        raise HTTPException(
            status_code=410,
            detail={
                "code": "deletion_already_processed",
                "message": "the deletion grace has already elapsed",
            },
        )
    user.deletion_scheduled_at = None
    db.add(user)
    # Event row joins the same transaction as the schedule clear so the
    # audit log stays consistent with the state.
    db.add(_build_account_event(user.id, "account.deletion_cancelled", {}))
    await db.flush()
    account_deletions_cancelled_total.inc()
    logger.info("auth.deletion_cancelled user_id={}", user.id)
    return Response(status_code=204)
