"""Authentication routes: magic-link flow, logout, and /me.

Endpoints
---------
POST /auth/magic-link    — request a magic link email
GET  /auth/callback      — exchange token → session cookie + redirect
POST /auth/logout        — clear session cookie
GET  /auth/me            — return current user + fresh CSRF token
GET  /me                 — top-level alias for /auth/me
"""

from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
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
    """
    settings = get_settings()
    existing_csrf = request.cookies.get(_CSRF_COOKIE_NAME, "")
    csrf_value = existing_csrf or secrets.token_hex(32)
    user_data = UserRead.model_validate(user).model_dump(mode="json")
    user_data["csrf_token"] = csrf_value

    response = JSONResponse(content=user_data)
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


# Top-level alias router — mounted at /api/v1/me so both
# /api/v1/me and /api/v1/auth/me resolve to the same handler.
me_router = APIRouter(tags=["auth"])


@me_router.get(
    "/me",
    response_model=UserRead,
    summary="Return the authenticated user (top-level alias)",
)
async def get_me_alias(
    request: Request,
    user: User = Depends(require_auth),
) -> JSONResponse:
    return _build_me_response(user, request)
