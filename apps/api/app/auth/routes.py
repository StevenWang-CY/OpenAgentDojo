"""Authentication routes: magic-link flow, logout, /me, and GitHub OAuth.

Endpoints
---------
POST /auth/magic-link        — request a magic link email
GET  /auth/callback          — exchange magic-link token → session cookie
POST /auth/logout            — clear session cookie
GET  /auth/me                — return current user + fresh CSRF token
POST /auth/csrf-refresh      — force-rotate the CSRF cookie
GET  /auth/github/available  — feature-flag probe for the FE button (P0-7)
GET  /auth/github/start      — initiate GitHub OAuth (P0-7)
GET  /auth/github/callback   — finish GitHub OAuth → session cookie (P0-7)

The top-level ``/me`` alias was retired (see main.py) because the FE only
calls ``/auth/me`` and the duplicate path doubled the OpenAPI surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from typing import Any

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
from app.auth.github_oauth import (
    OAUTH_STATE_COOKIE_NAME,
    GithubOAuthError,
    OAuthStateReplayError,
    build_authorize_url,
    consume_oauth_state,
    exchange_code_for_token,
    fetch_user_profile,
)
from app.auth.hashing import hash_email_for_event
from app.auth.magic_link import (
    consume_email_change_token,
    consume_magic_token,
    create_email_change_link,
    create_magic_link,
    magic_link_resend_db_fallback_wait_seconds,
    magic_link_resend_wait_seconds,
    record_magic_link_resend,
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
from app.schemas.auth import GithubOAuthAvailability
from app.schemas.consent import ConsentRecord, ConsentState, ConsentUpdate
from app.schemas.user import (
    CoachingConsentRead,
    CoachingConsentUpdate,
    DataExportRead,
    DeleteAccountRequest,
    DeletionLockError,
    DeletionScheduledRead,
    DisplayNameUpdate,
    EmailChangeConfirm,
    EmailChangeRequest,
    MeRecommendationInline,
    UserRead,
)

# Shared OpenAPI 403 declaration for every mutating P0-6 endpoint the
# DeletionLockMiddleware can intercept. Spelled out once so adding a new
# mutating route is a one-liner instead of a copy-paste hazard.
_DELETION_LOCK_RESPONSE: dict[int | str, dict[str, Any]] = {
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


# Anchor for fire-and-forget inline data-export tasks scheduled by the
# POST /me/data-export handler when the enqueue path is taken. Without
# this anchor the event loop is free to garbage-collect the task before
# the build finishes — the row would stay pinned at ``queued`` in the
# exact race the inline-race path is designed to rescue. The
# ``add_done_callback(discard)`` pattern matches the existing
# ``_BACKGROUND_TASKS`` set used by the reports/render path.
_EXPORT_RACE_TASKS: set[asyncio.Task[None]] = set()


class MagicLinkRequest(BaseModel):
    email: EmailStr
    # Phase 4.A.13 — optional same-origin relative path the callback
    # redirects to after minting the session cookie. Validated against the
    # same allowlist the GitHub OAuth callback uses (A.7); anything
    # outside the allowlist is silently dropped and the callback falls
    # back to ``/missions``. Storing the value on the token row means a
    # user clicking a deep-link from email lands on the page they came
    # from (e.g. ``/report/{uuid}``) instead of always bouncing to the
    # catalog.
    next: str | None = None


# ---------------------------------------------------------------------------
# POST /auth/magic-link
# ---------------------------------------------------------------------------


async def _send_magic_link_with_throttle(
    *,
    email: str,
    db: AsyncSession,
    request: Request,
    next_path: str | None = None,
) -> int:
    """Shared body for ``POST /auth/magic-link`` and ``/magic-link/resend``.

    Returns the number of seconds the caller must wait before another
    send will be honoured. ``0`` means "we just sent a link" (or
    suppressed it because the address is reserved as
    ``pending_email``); a positive value means we short-circuited and
    the caller should reuse the previously-sent link.

    Privacy invariants:

    * No distinction between "user exists" and "user does not exist" is
      surfaced — both paths return the same shape.
    * The throttled branch bumps
      ``magic_link_email_total{outcome="throttled"}`` so operators can
      monitor abuse rates — callers cannot tell from the response that
      they got throttled vs. that the send proceeded.
    """
    from loguru import logger as _logger

    from app.observability import magic_link_email_total, magic_link_throttled_total

    settings = get_settings()
    # P0-10 / Phase 4.A.12 — normalize at the route boundary so the
    # throttle key, the DB lookup, and the token row see the same
    # canonical value. CITEXT on Postgres masks case differences, but
    # the SQLite test path stores raw strings and the Redis throttle
    # key is a SHA-256 of the raw input — without normalization the
    # two paths drift.
    email = email.strip().lower()
    email_hash = hash_email_for_event(email, settings)

    # First consult Redis. If Redis is unavailable, fall through to the
    # DB-derived heuristic — the most-recent sign-in token row carries
    # the precise mint time via ``expires_at - magic_link_ttl_minutes``.
    # ``None`` distinguishes "Redis is down" from "Redis says no throttle"
    # (P1 / Phase 4.A.17). Only the down case forces the DB fallback.
    redis_wait = await magic_link_resend_wait_seconds(email)
    if redis_wait is None:
        wait = await magic_link_resend_db_fallback_wait_seconds(db, email)
    else:
        wait = redis_wait
    if wait > 0:
        # P1 / Phase 4.A.16 — dedicated counter so the "throttled" line
        # doesn't share the ``magic_link_email_total{backend="unknown"}``
        # bucket (which mis-labelled every throttle hit as an unknown
        # transport). Operators page off this counter independently.
        magic_link_throttled_total.inc()
        _logger.info(
            "auth.magic_link.throttled email_hash={} wait={}",
            email_hash,
            wait,
        )
        return wait

    # Past the throttle gate — proceed with the real send.
    base_url = (settings.web_origin or str(request.base_url)).rstrip("/")
    magic_url = await create_magic_link(db, email=email, base_url=base_url, next_path=next_path)

    # Commit the magic-link row BEFORE we await on outbound email so a
    # slow/wedged SMTP backend cannot pin this DB connection (and its
    # row-level locks) for the duration of the send timeout. The link is
    # persistent in the DB the moment we commit, so the user can retry
    # email delivery without losing the token.
    await db.commit()

    # Phase 4.A.5 — stamp the throttle IMMEDIATELY after the commit and
    # BEFORE awaiting the SMTP backend. This closes a race where two
    # concurrent ``/auth/magic-link/resend`` calls land within the
    # backend's own send window: under the old "stamp-after-send"
    # ordering the second caller saw an empty throttle (the first send
    # hadn't yet stamped), proceeded past the gate, and queued a
    # duplicate delivery. The throttle is advisory — it's stamped
    # regardless of whether SMTP actually delivers — so a transient
    # transport failure forces the user to either wait out the 60s
    # cooldown or click the link from the prior email (the token row
    # itself is durable post-commit).
    await record_magic_link_resend(email)
    _logger.info(
        "auth.magic_link.throttle_stamped email_hash={}",
        email_hash,
    )

    if magic_url is None:
        # ``create_magic_link`` returns ``None`` when the requested
        # address is currently reserved as ``pending_email`` on another
        # account (P0-6 reverse-direction TOCTOU defence). The throttle
        # is already stamped above; no SMTP dispatch on this branch.
        return 0

    try:
        delivered = await asyncio.wait_for(
            send_magic_link_email(
                to_email=email,
                magic_url=magic_url,
                settings=settings,
            ),
            timeout=10.0,
        )
    except TimeoutError:
        # Hashed email keeps the audit trail useful without leaking PII
        # (the redaction filter would mask the raw value anyway, but we
        # prefer to never even render it in the first place).
        _logger.warning(
            "[auth] magic-link email send timed out email_hash={} (token already persisted)",
            email_hash,
        )
        delivered = False

    if not delivered:
        _logger.error(
            "[auth] magic-link delivery FAILED email_hash={} (no backend confirmed delivery)",
            email_hash,
        )

    # Touch the legacy counter so anyone grepping for the old name still
    # gets a path to the rename. (No-op — kept as a reference, removed
    # entirely once dashboards migrate.)
    _ = magic_link_email_total
    return 0


def _safe_next_for_magic_link(raw: str | None) -> str | None:
    """Phase 4.A.13 — apply the shared FE-route allowlist to a magic-link ``next``.

    Returns the validated path or ``None``. Wrapped here so the route
    can pass the validated value into ``create_magic_link`` directly;
    invalid values are silently dropped (the callback then defaults to
    ``/missions``).
    """
    from app.auth.github_oauth import _validate_return_to

    return _validate_return_to(raw, get_settings())


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

    P0-10 — the resend-throttle window
    (:data:`MAGIC_LINK_RESEND_WINDOW_SECONDS`) is enforced on this path
    too. When the same address re-requests within the window we still
    return 204, but the email backend is NOT invoked. The
    ``Retry-After`` header on the response surfaces the precise wait
    time so the FE timer renders accurately without a separate
    round-trip.
    """
    wait = await _send_magic_link_with_throttle(
        email=str(body.email),
        db=db,
        request=request,
        next_path=_safe_next_for_magic_link(body.next),
    )
    response = Response(status_code=204)
    response.headers["Retry-After"] = str(wait)
    return response


class MagicLinkResendResponse(BaseModel):
    """JSON envelope for ``POST /auth/magic-link/resend`` (Phase 4.A.24 rename).

    Previously ``_ResendResponse`` — the underscore prefix tagged it as
    "private", but the schema is part of the public API contract and
    appears in ``openapi.json``. Rename keeps the wire shape identical
    (still a single ``wait_seconds: int``) but removes the misleading
    leading underscore.
    """

    wait_seconds: int


@router.post(
    "/magic-link/resend",
    response_model=MagicLinkResendResponse,
    status_code=200,
    summary=("Resend the magic-link email (60-second per-email cooldown, P0-10)"),
)
async def post_magic_link_resend(
    body: MagicLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Idempotent resend endpoint with a 60-second per-email cooldown.

    Semantics — identical to ``POST /auth/magic-link`` except:

    * The response body is ``{"wait_seconds": int}`` so the FE timer can
      render without parsing headers separately.
    * The ``Retry-After`` header is always set: ``0`` when a send
      proceeded, the remaining wait when throttled.
    * Privacy: the response shape is identical for "user exists" and
      "user does not exist" — the endpoint cannot be used as an
      account-existence oracle.
    * Idempotent: calling twice in the same window does not consume two
      slots; the second call returns the in-flight wait time.
    """
    wait = await _send_magic_link_with_throttle(
        email=str(body.email),
        db=db,
        request=request,
        next_path=_safe_next_for_magic_link(body.next),
    )
    response = JSONResponse(content={"wait_seconds": wait})
    response.headers["Retry-After"] = str(wait)
    return response


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
    result = await consume_magic_token(db, token)
    if result is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_magic_link",
                "message": "invalid or expired magic link",
            },
        )
    # Phase 4.A.13 — ``consume_magic_token`` returns ``(user, next_path)``.
    # Tolerate the legacy ``User`` return shape from any monkeypatched
    # test fixture so we don't regress existing tests.
    if isinstance(result, tuple):
        user, next_path = result
    else:
        user, next_path = result, None

    # Re-validate the persisted next_path against the live allowlist —
    # the token row may have been minted under an older policy that has
    # since tightened. Anything outside the allowlist falls back to
    # ``/missions``.
    from app.auth.github_oauth import _validate_return_to

    safe_next = _validate_return_to(next_path, settings)
    target_path = safe_next or "/missions"

    # Redirect to the validated target (absolute URL so the browser
    # goes to the web origin, not the API origin).
    frontend_url = f"{settings.web_origin.rstrip('/')}{target_path}"
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


def _build_me_response(
    user: User,
    request: Request,
    *,
    recommendation: MeRecommendationInline | None = None,
) -> JSONResponse:
    """Build the /me JSONResponse with CSRF token.

    P1-B27: ``/me`` is a *read* endpoint and used to mint a fresh CSRF token
    on every poll, which silently invalidated whatever the FE already had
    cached. We now reuse the existing cookie when one is present so the FE
    can keep working without re-fetching the body token.

    We keep the manual ``JSONResponse`` construction (rather than returning
    the ``UserRead`` model directly) because we still need to ``set_cookie``
    when minting a fresh token — letting FastAPI serialize would discard
    the cookie mutation.

    P1-2 — when ``recommendation`` is provided, the inline top-recommendation
    chip is surfaced under the ``recommendation`` key. The caller fetches
    the live recommendation via :func:`_fetch_inline_recommendation` and
    degrades to ``None`` on any cache/error path so the auth roundtrip
    never blocks on the recommendation engine.
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
            # P0-7 — verified-via-GitHub identity surface. The FE's
            # /account page renders the "Connected to GitHub" panel when
            # ``github_verified_at`` is non-null.
            "github_login": user.github_login,
            "github_avatar_url": user.github_avatar_url,
            "github_html_url": user.github_html_url,
            "github_verified_at": user.github_verified_at,
            "created_at": user.created_at,
            "last_login_at": user.last_login_at,
            "csrf_token": csrf_value,
            "tutorial_completed_at": user.tutorial_completed_at,
            "tutorial_replay_count": user.tutorial_replay_count,
            # P0-6 — surfaces the in-flight email change banner + the
            # 7-day deletion countdown on the FE /account page.
            "pending_email": user.pending_email,
            "deletion_scheduled_at": user.deletion_scheduled_at,
            "recommendation": recommendation,
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


async def _fetch_inline_recommendation(
    db: AsyncSession, user: User
) -> MeRecommendationInline | None:
    """Resolve the inline top recommendation for ``GET /auth/me``.

    Wraps :func:`app.recommendations.cache.get_cached_or_compute` with a
    defensive try/except so any cache miss + recommendation-side fault
    degrades to ``None`` rather than 5xx'ing the auth roundtrip. The FE
    renders the header chip only when the field is present.
    """
    try:
        from app.recommendations.cache import get_cached_or_compute

        rec_set = await get_cached_or_compute(db, user.id)
        if not rec_set.recommendations:
            return None
        top = rec_set.recommendations[0]
        if top.status != "shipped":
            return None
        return MeRecommendationInline(
            mission_id=top.mission_id,
            title=top.title,
            language=top.language,
        )
    except Exception:  # pragma: no cover — never block /auth/me
        logger.exception(
            "[auth.me] inline_recommendation_failed user_id={}",
            user.id,
        )
        return None


@router.get(
    "/me",
    response_model=UserRead,
    summary="Return the authenticated user",
    responses={
        401: {
            "description": (
                "No session cookie was presented (or the cookie failed "
                "verification). Sign in via ``POST /auth/magic-link`` or "
                "the GitHub OAuth flow and retry."
            ),
        },
    },
)
async def get_me(
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    recommendation = await _fetch_inline_recommendation(db, user)
    return _build_me_response(user, request, recommendation=recommendation)


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
        (
            await db.execute(
                select(UserConsent)
                .where(UserConsent.user_id == user.id)
                .order_by(UserConsent.granted_at.desc())
            )
        )
        .scalars()
        .all()
    )

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


# ---------------------------------------------------------------------------
# GET /auth/me/coaching-consent  +  POST /auth/me/coaching-consent  (P1-4)
# ---------------------------------------------------------------------------
#
# Surface for the per-user "send my scratchpad text to AWS Bedrock for the
# coaching reflection" toggle. The column lives on ``users``
# (``coaching_reflections_enabled``); Wave 2B's coaching endpoint reads it
# before forwarding the scratchpad body, so the toggle is load-bearing for
# the privacy disclosure (see /legal/privacy §1 "Workspace scratchpad text"
# and §4 "Amazon Web Services (AWS Bedrock)"). Default is True for both new
# and backfilled rows — see migration 0031.
#
# We keep these on the same ``/me/*`` shape as the existing P0-5 consent
# endpoints rather than overloading PATCH /me, which only mutates
# ``display_name`` (and whose schema is ``extra='forbid'``). A dedicated
# POST gives the FE a single-bit endpoint that's cheap to invalidate and
# doesn't force a re-fetch of the whole UserRead.


@router.get(
    "/me/coaching-consent",
    response_model=CoachingConsentRead,
    summary=(
        "Return whether the caller has opted in to scratchpad coaching "
        "reflections (P1-4)"
    ),
)
async def get_me_coaching_consent(
    user: User = Depends(require_auth),
) -> CoachingConsentRead:
    """Read the caller's coaching opt-in bit.

    Read-only; safe to call on every render of the privacy panel.
    """
    return CoachingConsentRead(
        coaching_reflections_enabled=bool(user.coaching_reflections_enabled),
    )


@router.post(
    "/me/coaching-consent",
    response_model=CoachingConsentRead,
    summary=(
        "Toggle the scratchpad coaching reflection opt-in for the caller "
        "(P1-4)"
    ),
    responses=_DELETION_LOCK_RESPONSE,
)
async def post_me_coaching_consent(
    body: CoachingConsentUpdate,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> CoachingConsentRead:
    """Persist the toggle and return the new state.

    Idempotent — POSTing the value the column already carries is a
    no-op. The route deliberately does NOT emit an account-event row:
    the toggle is a preference, not an audit-grade consent decision
    (those live in :class:`UserConsent` and are append-only with a
    policy_version stamp). If we ever need an audit trail here we can
    add an ``account_events`` row under a new event-type literal in a
    future migration without changing this endpoint.

    Cache invalidation on opt-out: when the new value is ``False`` we
    eagerly wipe the user's ``scratchpad_coaching`` cache rows via
    the same JOIN the account-deletion worker uses
    (``coaching_cache_user_index`` → ``llm_cache``). Rows shared with
    other users — discovered by finding any OTHER index row pointing
    at the same cache id — are preserved; the shared-row counter
    surfaces the preservation for ops dashboards. The cache rows are
    keyed by content hash (not user id), so a coincidental second
    user with identical inputs keeps their cache hot.
    """
    from app.models.coaching_cache_user_index import CoachingCacheUserIndex
    from app.models.llm_cache import LLMCache
    from app.observability import llm_cache_shared_row_retained_total

    previously_enabled = bool(user.coaching_reflections_enabled)
    user.coaching_reflections_enabled = bool(body.coaching_reflections_enabled)
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # Invalidate the user's coaching cache only on the True → False
    # transition. The reverse direction (False → True) needs no cache
    # touch — the user simply starts hitting the endpoint again, and
    # the chokepoint resolves a fresh hash on next call.
    if previously_enabled and not user.coaching_reflections_enabled:
        from sqlalchemy import delete as sa_delete

        candidate_cache_ids = list(
            (
                await db.execute(
                    select(CoachingCacheUserIndex.llm_cache_id).where(
                        CoachingCacheUserIndex.user_id == user.id
                    )
                )
            ).scalars()
        )

        # Per-row decision: only delete the cache row when this user
        # is the *only* remaining index row pointing at it. A shared
        # row stays, the user's index row goes either way.
        to_delete: list = []
        retained = 0
        for cache_id in candidate_cache_ids:
            others = (
                await db.execute(
                    select(CoachingCacheUserIndex.user_id)
                    .where(
                        CoachingCacheUserIndex.llm_cache_id == cache_id,
                        CoachingCacheUserIndex.user_id != user.id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if others is None:
                to_delete.append(cache_id)
            else:
                retained += 1

        if to_delete:
            await db.execute(
                sa_delete(LLMCache).where(LLMCache.id.in_(to_delete))
            )
        # Always drop the user's index rows regardless of preservation —
        # the user has opted out, the index link should not survive.
        if candidate_cache_ids:
            await db.execute(
                sa_delete(CoachingCacheUserIndex).where(
                    CoachingCacheUserIndex.user_id == user.id
                )
            )
        await db.flush()

        if retained:
            llm_cache_shared_row_retained_total.inc(retained)
        logger.info(
            "auth.coaching_consent.cache_invalidated user_id={} "
            "deleted_cache_rows={} retained_shared_rows={}",
            user.id,
            len(to_delete),
            retained,
        )

    logger.info(
        "auth.coaching_consent.set user_id={} enabled={}",
        user.id,
        user.coaching_reflections_enabled,
    )
    return CoachingConsentRead(
        coaching_reflections_enabled=bool(user.coaching_reflections_enabled),
    )


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


def _build_account_event(user_id, event_type: str, payload: dict) -> AccountEvent:
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
        refetched = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
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
                "message": ("This email was claimed by another account before you could confirm."),
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
    enqueued = False
    if queue is not None:
        try:
            queue.enqueue("app.workers.account_export.build_user_export", str(export.id))
            enqueued = True
        except Exception as exc:
            logger.warning("data-export enqueue failed for {}, running inline: {}", export.id, exc)

    if not enqueued:
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
    else:
        # Belt + braces: schedule an inline build to RACE the queued
        # worker. ``_async_build_user_export`` short-circuits on terminal
        # states (READY/FAILED) so whichever finishes second is a
        # cheap no-op. This rescues the row when:
        #
        # - Worker.count reported >0 against a stale registry (the
        #   worker process is gone but its registration hasn't expired).
        # - The worker accepted the job but wedged before transitioning
        #   the row to ``running``.
        # - The queue is degraded but the enqueue itself succeeded.
        #
        # The schedule is fire-and-forget — we don't await it, so the
        # POST handler still returns the seeded ``queued`` envelope
        # quickly. The task is anchored to a module-level set so the
        # event loop doesn't finalise it mid-flight.
        task = asyncio.create_task(
            asyncio.to_thread(build_user_export, str(export.id), inline=True),
            name=f"data-export-inline-race-{export.id}",
        )
        _EXPORT_RACE_TASKS.add(task)
        task.add_done_callback(_EXPORT_RACE_TASKS.discard)

    return DataExportRead.model_validate(export)


# ---------------------------------------------------------------------------
# GET /auth/me/data-export/latest
# ---------------------------------------------------------------------------


@router.get(
    "/me/data-export/latest",
    response_model=DataExportRead,
    summary="Discover the most recent data-export for this user (P0-6)",
    responses={204: {"description": "no exports exist for this user"}},
)
async def get_me_data_export_latest(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return the caller's most-recent ``DataExport`` (by ``requested_at``
    desc) so the panel can adopt an existing in-flight or terminal row on
    mount.

    Returns 204 if no export rows exist — the panel renders the empty
    "Request export" state in that case. The 204 must come back as a
    ``Response`` instance (FastAPI swallows ``None`` returns through a
    typed response_model and emits an empty 200 JSON body, which trips
    the FE's parser).

    Why this exists: the panel previously stored the export id in local
    React state seeded only by a successful POST. After a page reload,
    state was empty so the empty "No exports yet" copy rendered — but
    any pre-existing queued/running row in the DB caused POST to 409
    with "export_in_flight". The user saw two contradictory messages on
    the same panel. This endpoint plus the 409 ``detail.export_id`` field
    let the FE recover the existing row deterministically.
    """
    from datetime import UTC, datetime

    from app.models.data_export import (
        EXPORT_STATUS_EXPIRED,
        EXPORT_STATUS_READY,
        DataExport,
    )

    export = (
        await db.execute(
            select(DataExport)
            .where(DataExport.user_id == user.id)
            .order_by(DataExport.requested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if export is None:
        return Response(status_code=204)

    now = datetime.now(UTC)
    download_url: str | None = None
    if export.status == EXPORT_STATUS_READY:
        expires_at = export.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at is not None and expires_at <= now:
            export.status = EXPORT_STATUS_EXPIRED
            db.add(export)
            await db.flush()
            await db.commit()
        elif export.s3_key and expires_at is not None:
            remaining = max(1, int((expires_at - now).total_seconds()))
            from app.storage import generate_download_url

            download_url = generate_download_url(export.s3_key, expires_in=remaining)

    payload = DataExportRead.model_validate(export)
    if download_url is not None:
        payload = payload.model_copy(update={"download_url": download_url})
    return JSONResponse(
        content=payload.model_dump(mode="json"),
        status_code=200,
    )


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
# POST /auth/me/data-export/{export_id}/kick
# ---------------------------------------------------------------------------


@router.post(
    "/me/data-export/{export_id}/kick",
    response_model=DataExportRead,
    summary="Force an inline run of a queued data-export row (P0-6 recovery)",
)
async def post_me_data_export_kick(
    export_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> DataExportRead:
    """Manual recovery hatch for a row that is stuck in ``queued`` or
    ``running`` past the normal worker completion window.

    Three failure modes this endpoint covers — all observable as the FE
    poll showing "Queued — your export will start shortly" for minutes:

    * **No RQ worker consumer**: the request handler enqueued the job
      but no ``rq worker`` process is consuming the queue. ``get_queue``
      now detects this and falls through to the inline fallback, but
      pre-existing queued rows from before that fix exist in the wild.
    * **Worker registered but dead**: ``Worker.count`` returns >0
      because the worker process registered itself in Redis before
      dying. The job sits in Redis until the heartbeat expires
      (default 7 minutes).
    * **Worker present but stuck**: a previous build deadlocked on
      something. The 60s sweep eventually rescues the row, but the
      user may not want to wait.

    The endpoint runs ``build_user_export(inline=True)`` synchronously
    in the request loop (via ``asyncio.to_thread`` to keep the loop
    unblocked). On return the row carries the terminal status (ready
    / failed) and the response surfaces it directly.

    Owner-only by uuid; cross-user kicks return 404. Idempotent against
    terminal rows: ``ready`` / ``failed`` short-circuit inside the
    worker itself (see ``_async_build_user_export``).
    """
    import asyncio as _asyncio
    import uuid as _uuid

    from app.models.data_export import (
        EXPORT_IN_FLIGHT_STATUSES,
        DataExport,
    )
    from app.workers.account_export import build_user_export

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

    if export.status not in EXPORT_IN_FLIGHT_STATUSES:
        # Already terminal — just return the current state. The FE only
        # exposes the kick button while polling so this branch is
        # defensive; surfacing a 409 would be punishing for a race.
        return DataExportRead.model_validate(export)

    logger.info(
        "auth.data_export_kick user_id={} export_id={} prior_status={}",
        user.id,
        export.id,
        export.status,
    )

    try:
        await _asyncio.to_thread(build_user_export, str(export.id), inline=True)
    except Exception as exc:
        # inline=True suppresses re-raises inside the worker — anything
        # bubbling out is structural (thread pool, asyncio.run nesting,
        # AsyncSessionLocal misconfigured). Log loudly and let the
        # refresh below surface whatever state the row landed at.
        logger.warning(
            "data-export kick raised despite inline=True for {}: {}",
            export.id,
            exc,
        )

    await db.refresh(export)
    return DataExportRead.model_validate(export)


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


# ===========================================================================
# P0-7 — GitHub OAuth identity verification
# ===========================================================================
#
# Three endpoints under ``/api/v1/auth/github/*`` implement the OAuth
# round-trip with github.com. Design notes live in docs/security.md; in
# short:
#
#   1. ``GET /auth/github/available`` — feature-flag probe. Returns
#      ``{enabled: bool}`` so the FE can hide the button when the operator
#      hasn't configured GITHUB_OAUTH_CLIENT_ID/SECRET.
#   2. ``GET /auth/github/start?return_to=…`` — mints a state JWT
#      (cookie-bound, 10-min TTL) and 302s to github.com/login/oauth.
#   3. ``GET /auth/github/callback?code=…&state=…`` — verifies state,
#      exchanges code for access token, fetches /user + /user/emails,
#      upserts users by github_id (preferred) or by email (link path),
#      mints a session cookie, redirects to the FE.
#
# Failure mode (any GithubOAuthError) → redirect to
# ``/auth/sign-in?error=github_oauth_failed`` with a structured log line.
# We never echo GitHub error strings back to the user.


@router.get(
    "/github/available",
    response_model=GithubOAuthAvailability,
    summary="Whether GitHub OAuth is configured on this deployment (P0-7)",
)
async def get_github_oauth_available() -> GithubOAuthAvailability:
    """Cheap probe used by the FE sign-in page.

    Returns ``{enabled: true}`` only when both ``GITHUB_OAUTH_CLIENT_ID``
    and ``GITHUB_OAUTH_CLIENT_SECRET`` are set. The FE hides the
    "Continue with GitHub" button when ``enabled`` is False so users
    aren't shown a path that would 503 on click.
    """
    return GithubOAuthAvailability(enabled=get_settings().github_oauth_enabled)


@router.get(
    "/github/start",
    summary="Begin the GitHub OAuth round-trip (P0-7)",
)
async def get_github_oauth_start(
    request: Request,
    return_to: str | None = Query(
        default=None,
        description=(
            "Optional relative path the callback will redirect to after "
            "minting the session cookie. Must start with ``/`` and not "
            "with ``//``; otherwise dropped."
        ),
    ),
) -> Response:
    """Mint the state cookie and 302 to github.com/login/oauth/authorize.

    Returns 503 with ``{code: 'oauth_unavailable'}`` when the operator has
    not configured client_id + client_secret. The FE's
    ``GET /auth/github/available`` probe is the primary defence against
    showing the button in that case — this 503 is the defence-in-depth
    fallback for clients that hit the URL directly.
    """
    settings = get_settings()
    if not settings.github_oauth_enabled:
        return JSONResponse(
            status_code=503,
            content={
                "code": "oauth_unavailable",
                "message": "github oauth is not configured on this deployment",
            },
        )

    # Build the redirect response FIRST so ``issue_oauth_state`` can attach
    # the cookie to it (RedirectResponse → 302). The state value is
    # embedded in the URL we redirect to so GitHub can echo it back.
    redirect = RedirectResponse(url="", status_code=302)
    try:
        state = _issue_state_with_response(redirect, settings, return_to=return_to)
        authorize_url = build_authorize_url(state, settings)
    except GithubOAuthError as exc:
        # Should be unreachable because we checked github_oauth_enabled
        # above, but keep the typed-error path consistent with the rest of
        # the module.
        logger.warning("auth.github.start failed: {}", exc.message)
        return JSONResponse(
            status_code=503,
            content={
                "code": exc.code,
                "message": "github oauth is not configured on this deployment",
            },
        )
    redirect.headers["location"] = authorize_url
    logger.info(
        "auth.github.start.redirect ip={}",
        request.client.host if request.client else "unknown",
    )
    return redirect


def _issue_state_with_response(response: Response, settings, *, return_to: str | None) -> str:
    """Indirection so the route handler can stay declarative.

    Pure pass-through to :func:`app.auth.github_oauth.issue_oauth_state` —
    isolated here so the test suite can monkeypatch this symbol if it
    wants to assert on cookie attributes without re-implementing the JWT
    minting logic.
    """
    from app.auth.github_oauth import issue_oauth_state as _issue

    return _issue(response, settings, return_to=return_to)


@router.get(
    "/github/callback",
    summary="Complete the GitHub OAuth round-trip (P0-7)",
)
async def get_github_oauth_callback(
    request: Request,
    code: str = Query(..., description="GitHub authorization code"),
    state: str = Query(..., description="State value echoed by GitHub"),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Verify state, exchange code, upsert user, mint session, redirect.

    The "happy path" is:

      1. ``consume_oauth_state`` verifies the state cookie matches the
         ``?state=`` query and the JWT is unexpired.
      2. ``exchange_code_for_token`` POSTs to github.com → access token.
      3. ``fetch_user_profile`` GETs ``/user`` + ``/user/emails`` and
         returns a normalised ``GithubProfile``.
      4. We upsert the local user by ``github_id`` (primary key), falling
         back to ``email`` for first-time link, falling back to a new row
         if neither matches.
      5. Mint a session cookie, set CSRF, redirect to
         ``web_origin + (return_to or '/missions')``.

    Any failure (state mismatch, expired JWT, GitHub 4xx/5xx, network
    error) is logged with a structured ``auth.github.callback.failure``
    line and redirects to
    ``web_origin/auth/sign-in?error=github_oauth_failed``. We never echo
    GitHub error strings back to the browser.
    """
    settings = get_settings()
    if not settings.github_oauth_enabled:
        return _github_oauth_error_redirect(settings, "oauth_unavailable")

    # 1. Verify state. ``consume_oauth_state`` is async after Phase 4.A.6
    # so the nonce single-use SETNX can await the Redis client.
    try:
        state_payload = await consume_oauth_state(request, settings, presented_state=state)
    except OAuthStateReplayError as exc:
        # Replay attempts get the same generic redirect every other OAuth
        # failure does — but with a distinct log line so dashboards can
        # alert on the pattern. Replays come from either a re-used
        # bookmark / "back" navigation (benign double-click) or an
        # attacker re-presenting a stolen callback URL (a real attack);
        # we don't differentiate at the API layer.
        logger.warning(
            "auth.github.callback.failure stage=state code={} message={}",
            exc.code,
            exc.message,
        )
        return _github_oauth_error_redirect(settings, exc.code)
    except HTTPException as exc:
        raw_detail = exc.detail
        detail: dict[str, object] = raw_detail if isinstance(raw_detail, dict) else {}
        code_value = str(detail.get("code", "oauth_state_invalid"))
        logger.warning("auth.github.callback.failure stage=state code={}", code_value)
        return _github_oauth_error_redirect(settings, code_value)

    # Phase 4.A.7 — re-validate the embedded ``return_to`` even though
    # ``issue_oauth_state`` already filtered it at mint time. The state
    # cookie is signed (so the value cannot be tampered with by the
    # browser), but the allowlist may have tightened between mint and
    # consume, and a stale cookie minted under an older allowlist must
    # NOT pass through. Anything outside the current allowlist falls
    # back to ``/missions``.
    from app.auth.github_oauth import _validate_return_to

    raw_return_to = state_payload.get("return_to")
    return_to = _validate_return_to(
        raw_return_to if isinstance(raw_return_to, str) else None,
        settings,
    )
    if return_to is None:
        return_to = "/missions"

    # 2-3. Exchange code + fetch profile.
    try:
        access_token = await exchange_code_for_token(code, settings)
        profile = await fetch_user_profile(access_token)
    except GithubOAuthError as exc:
        logger.warning(
            "auth.github.callback.failure stage=github code={} message={}",
            exc.code,
            exc.message,
        )
        return _github_oauth_error_redirect(settings, exc.code)

    # 4. Upsert. Three branches:
    #    a) row exists with the same github_id → merge / refresh fields.
    #    b) row exists with the same email but no github_id → link.
    #    c) no row → create new row with handle derived from email/login.
    user, linkage = await _upsert_user_from_github(db, profile)
    await db.flush()
    await db.commit()

    # 5. Mint session cookie, CSRF, redirect.
    redirect_url = f"{settings.web_origin.rstrip('/')}{return_to}"
    response = RedirectResponse(url=redirect_url, status_code=302)
    mint_session_cookie_for_user(response, user, settings)
    issue_csrf_token(response, settings)
    # Clear the short-lived state cookie now that we've consumed it.
    response.delete_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    logger.info(
        "auth.github.callback.success user_id={} github_id={} linkage={}",
        user.id,
        profile.github_id,
        linkage,
    )
    return response


def _github_oauth_error_redirect(settings, code: str) -> RedirectResponse:
    """Build the standard sign-in error redirect for an OAuth failure.

    Always lands on ``/auth/sign-in?error=github_oauth_failed`` so the FE
    can render a single, generic error toast (GitHub error strings are
    not safe to surface — they can leak internal identifiers). The
    ``code`` parameter is kept for the log line that the caller emits
    immediately before this helper.

    Phase 4.A.23 — also clears the OAUTH_STATE_COOKIE on every failure
    path so a stale state cookie can't shadow a subsequent retry (the
    callback otherwise reads the old cookie, mismatches the fresh
    ``?state=`` GitHub echoes back, and the user sees a second
    ``oauth_state_mismatch`` failure that's confusing to debug).
    """
    _ = code  # logged by the caller, retained for symmetry with the redirect URL
    base = settings.web_origin.rstrip("/")
    response = RedirectResponse(
        url=f"{base}/auth/sign-in?error=github_oauth_failed",
        status_code=302,
    )
    response.delete_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return response


async def _upsert_user_from_github(
    db: AsyncSession,
    profile,
) -> tuple[User, str]:
    """Upsert a :class:`User` row from a fetched :class:`GithubProfile`.

    Returns the user + a ``linkage`` string the route logs:

      * ``"merged"`` — matched by ``github_id`` (the canonical key). The
        same GitHub identity is re-signing in; we refresh login/avatar/
        html_url/verified_at in case the user renamed on github.com or
        rotated their avatar.
      * ``"linked"`` — matched by email but ``github_id`` was unset. The
        user previously signed up via magic-link; we attach the verified
        GitHub identity now.
      * ``"new"`` — no existing user, create a fresh row. Handle is
        derived from the GitHub login (lowercased + sanitised); collisions
        fall through to the standard ``-2``, ``-3``, … suffix path.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    from app.auth.magic_link import _allocate_handle, _slugify_handle

    now = _datetime.now(_UTC)

    # Phase 4.A.12 — normalize at the boundary. ``fetch_user_profile``
    # already lower-cases the primary email it picks, but defensive
    # normalization here keeps the SQLite test path (where the column
    # is plain TEXT, not CITEXT) consistent with the Postgres
    # case-insensitive upsert.
    profile_email = (profile.email or "").strip().lower()

    # a) by github_id (preferred).
    existing_by_id = (
        await db.execute(select(User).where(User.github_id == profile.github_id))
    ).scalar_one_or_none()
    if existing_by_id is not None:
        existing_by_id.github_login = profile.login
        existing_by_id.github_avatar_url = profile.avatar_url
        existing_by_id.github_html_url = profile.html_url
        existing_by_id.github_verified_at = now
        existing_by_id.last_login_at = now
        db.add(existing_by_id)
        return existing_by_id, "merged"

    # b) by email (link path).
    existing_by_email = (
        await db.execute(select(User).where(User.email == profile_email))
    ).scalar_one_or_none()
    if existing_by_email is not None:
        existing_by_email.github_id = profile.github_id
        existing_by_email.github_login = profile.login
        existing_by_email.github_avatar_url = profile.avatar_url
        existing_by_email.github_html_url = profile.html_url
        existing_by_email.github_verified_at = now
        existing_by_email.last_login_at = now
        # Backfill display_name from GitHub's ``name`` field if the user
        # never set one locally — a tiny ergonomic win on first link.
        if not existing_by_email.display_name and profile.name:
            existing_by_email.display_name = profile.name[:120]
        db.add(existing_by_email)
        return existing_by_email, "linked"

    # c) no row — create fresh.
    base_handle = (
        _slugify_handle(profile_email)
        if profile_email
        else (_slugify_handle(profile.login) or "user")
    )
    # Prefer the GitHub login when it produces a non-default slug — it
    # tends to be more recognisable on the public profile URL.
    github_slug = _slugify_handle(profile.login)
    if github_slug and github_slug != "user":
        base_handle = github_slug
    handle = await _allocate_handle(db, base_handle)
    display_name = profile.name[:120] if profile.name else None
    user = User(
        email=profile_email,
        handle=handle,
        display_name=display_name,
        github_id=profile.github_id,
        github_login=profile.login,
        github_avatar_url=profile.avatar_url,
        github_html_url=profile.html_url,
        github_verified_at=now,
        last_login_at=now,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # Race: a concurrent magic-link or another OAuth callback inserted
        # the same email between our SELECT and INSERT. Roll back, re-fetch,
        # then attach github_id to the now-existing row.
        await db.rollback()
        refetched = (
            await db.execute(select(User).where(User.email == profile_email))
        ).scalar_one_or_none()
        if refetched is None:
            raise
        refetched.github_id = profile.github_id
        refetched.github_login = profile.login
        refetched.github_avatar_url = profile.avatar_url
        refetched.github_html_url = profile.html_url
        refetched.github_verified_at = now
        refetched.last_login_at = now
        if not refetched.display_name and profile.name:
            refetched.display_name = profile.name[:120]
        db.add(refetched)
        return refetched, "linked"
    return user, "new"
