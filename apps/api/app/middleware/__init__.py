"""Cross-cutting ASGI middleware for the OpenAgentDojo API.

Each module here adds one orthogonal concern (CSRF, rate-limiting, banned
commands, security headers, trusted-host validation). They are wired in
``app.main.create_app`` in a specific order — see that module for details.
"""

from app.middleware.banned_commands import BannedCommandsMiddleware
from app.middleware.csrf import CSRFMiddleware
from app.middleware.deletion_lock import DeletionLockMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.trusted_host import build_trusted_host_middleware

__all__ = [
    "REQUEST_ID_HEADER",
    "BannedCommandsMiddleware",
    "CSRFMiddleware",
    "DeletionLockMiddleware",
    "RateLimitMiddleware",
    "RequestIdMiddleware",
    "SecurityHeadersMiddleware",
    "build_trusted_host_middleware",
]
