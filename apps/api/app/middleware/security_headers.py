"""Add baseline browser security headers to every response.

Covers CSP, framing, MIME sniffing, referrer policy, and permissions policy.
The CSP is conservative for an API-only surface (no rendered HTML in prod);
it still allows the OpenAPI docs UI under ``/docs`` to load by permitting
``'unsafe-inline'`` for script/style — FastAPI's Swagger UI requires it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.config import get_settings


def _build_csp(web_origin: str) -> str:
    # ``img-src`` allows ``https:`` so OG-image scrapers (Slack/Twitter/etc.)
    # can hydrate the social preview from any signed CDN URL we return.
    return (
        "default-src 'self'; "
        f"connect-src 'self' {web_origin} ws: wss:; "
        "img-src 'self' data: https:; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'"
    )


_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a stable set of security headers on every response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        settings = get_settings()
        csp = _build_csp(settings.web_origin)

        # Avoid clobbering headers that a handler has intentionally set.
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", _PERMISSIONS_POLICY)

        # HSTS only when cookies require HTTPS — forcing it on dev (HTTP
        # localhost) would lock developers out of their own machine for the
        # max-age window.
        if settings.cookie_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )

        return response
