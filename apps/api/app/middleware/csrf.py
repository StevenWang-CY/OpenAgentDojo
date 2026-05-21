"""CSRF double-submit cookie middleware.

On unsafe methods (POST/PUT/PATCH/DELETE) the request MUST present a
``X-CSRF-Token`` header whose value matches the ``arena_csrf`` cookie.

Carve-outs (no cookie is available yet):
  - ``POST /api/v1/auth/magic-link`` — the user has no session yet.
  - ``GET  /api/v1/auth/callback``   — handled by GET, not subject to this check.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.auth.csrf import validate_csrf
from app.config import get_settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Path suffixes that are exempt from the CSRF check because the client
# cannot yet have a CSRF cookie (or the route is not a state mutation).
_EXEMPT_PATHS = (
    "/api/v1/auth/magic-link",
    "/api/v1/auth/callback",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject unsafe requests that fail the double-submit CSRF check."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        method = request.method.upper()
        if method not in _UNSAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        # Exact match only — endswith was a footgun (e.g. ``/api/v1/auth/magic-link``
        # would also exempt any future ``…/auth/magic-link`` sub-route).
        if path in _EXEMPT_PATHS:
            return await call_next(request)

        # WebSocket upgrade requests are GET, but keep an explicit guard.
        if path.startswith("/ws/"):
            return await call_next(request)

        settings = get_settings()
        if not validate_csrf(request, settings):
            return JSONResponse(
                status_code=403,
                content={"detail": "csrf token missing or invalid", "code": "csrf_invalid"},
            )

        return await call_next(request)
