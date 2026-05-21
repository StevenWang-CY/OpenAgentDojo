"""Session cookie helpers — issue, read, and revoke the HTTP-only session cookie.

The cookie value is a ``python-jose`` signed JWT with a 30-day expiry.  We use
HS256 with ``settings.session_secret`` as the signing key.

Cookie attributes:
  - HttpOnly: True    — not readable by JS (anti-XSS)
  - Secure: False     — HTTP-OK for local dev; set True in prod via your reverse proxy
  - SameSite: lax     — CSRF protection while still allowing top-level navigations
  - Max-Age: 30 days
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from jose import JWTError, jwt
from loguru import logger

from app.config import Settings

_ALGORITHM = "HS256"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days in seconds


def issue_session_cookie(response: Response, user_id: str, settings: Settings) -> None:
    """Sign a JWT and attach it as an HttpOnly session cookie on *response*."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=30)).timestamp()),
    }
    token = jwt.encode(payload, settings.session_secret, algorithm=_ALGORITHM)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )


def get_user_id_from_cookie(request: Request, settings: Settings) -> str | None:
    """Decode and verify the session JWT.  Returns the subject or None on any error."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.session_secret, algorithms=[_ALGORITHM])
        user_id: str | None = payload.get("sub")
        return user_id or None
    except JWTError as exc:
        logger.debug("session cookie decode error: {}", exc)
        return None
    except Exception as exc:  # pragma: no cover
        logger.warning("unexpected session cookie error: {}", exc)
        return None


def revoke_session_cookie(response: Response, settings: Settings) -> None:
    """Delete the session cookie by setting Max-Age=0."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
    )
