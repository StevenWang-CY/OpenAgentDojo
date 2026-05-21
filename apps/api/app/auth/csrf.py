"""CSRF protection via a per-session double-submit cookie.

The CSRF token lives in a *non-HttpOnly* cookie called ``arena_csrf`` so that
the frontend JavaScript can read it and echo it back in the ``X-Csrf-Token``
request header.  The API then compares header vs cookie to validate.

The token is intentionally **not** signed — it only needs to be unguessable
(32 random hex bytes ≈ 128 bits of entropy).  SameSite=Lax on the session
cookie already mitigates simple CSRF; this layer is a defence-in-depth measure
for mutations that originate from a cross-origin context.
"""

from __future__ import annotations

import secrets

from fastapi import Request, Response

from app.config import Settings

_CSRF_COOKIE_NAME = "arena_csrf"
_CSRF_HEADER_NAME = "X-Csrf-Token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def issue_csrf_token(response: Response, settings: Settings) -> str:
    """Generate a new CSRF token, set it in a readable cookie, and return the value."""
    token = secrets.token_hex(32)
    response.set_cookie(
        key=_CSRF_COOKIE_NAME,
        value=token,
        httponly=False,  # JS must be able to read this
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )
    return token


def validate_csrf(request: Request, settings: Settings) -> bool:
    """Return True when the header and cookie CSRF tokens match (constant-time)."""
    header_token = request.headers.get(_CSRF_HEADER_NAME, "")
    cookie_token = request.cookies.get(_CSRF_COOKIE_NAME, "")
    if not header_token or not cookie_token:
        return False
    return secrets.compare_digest(header_token, cookie_token)
