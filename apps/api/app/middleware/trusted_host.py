"""Thin factory around Starlette's :class:`TrustedHostMiddleware`.

The middleware ships in Starlette but we keep a single chokepoint that
reads ``settings.allowed_hosts_list`` so config changes flow through one
spot. In development we accept any host; staging/production are required to
set an explicit list (enforced in ``Settings._validate_for_environment``).
"""

from __future__ import annotations

from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings


def build_trusted_host_middleware(settings: Settings) -> Middleware:
    """Return a Starlette ``Middleware`` ready to pass into ``add_middleware``."""
    return Middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts_list,
    )
