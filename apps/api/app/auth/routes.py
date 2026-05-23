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
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from loguru import logger
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import _COOKIE_MAX_AGE, _CSRF_COOKIE_NAME, issue_csrf_token
from app.auth.deps import require_auth
from app.auth.email import send_magic_link_email
from app.auth.magic_link import consume_magic_token, create_magic_link
from app.auth.session_cookie import issue_session_cookie, revoke_session_cookie
from app.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserRead

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
        _logger.warning(
            "[auth] magic-link email send timed out for {} — token already persisted",
            str(body.email),
        )
        delivered = False

    if not delivered:
        _logger.error(
            "[auth] magic-link delivery FAILED for {} — no backend confirmed delivery",
            str(body.email),
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
            detail="invalid or expired magic link",
        )

    # Redirect to the frontend /missions page (absolute URL so the browser
    # goes to the web origin, not the API origin).
    frontend_url = f"{settings.web_origin.rstrip('/')}/missions"
    redirect = RedirectResponse(url=frontend_url, status_code=302)
    issue_session_cookie(redirect, str(user.id), settings)
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

            payload = _jwt.decode(
                raw, settings.session_secret, algorithms=["HS256"]
            )
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


# The top-level ``me_router`` (which mounted ``/api/v1/me`` as an alias for
# ``/api/v1/auth/me``) was removed: the FE only ever hits ``/auth/me`` and
# shipping two paths for one handler confused the OpenAPI → TS generator
# (it minted two equivalent operation IDs). Re-add via main.py if a new
# client ever needs the top-level shape.
