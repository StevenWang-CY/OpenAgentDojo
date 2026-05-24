"""Deletion-grace lockout (P0-6).

While a user has ``users.deletion_scheduled_at IS NOT NULL`` (the 7-day
grace window), every state-changing request returns 403 except the
explicit ``/me/delete/cancel`` exit hatch. The user can still sign in
(so they can reach the cancel endpoint) and can still read their data —
this is read-only mode, not full lockout.

Ordering invariant
------------------
This middleware MUST execute *after* the CSRF middleware so a forged
request from another origin can't trigger the lockout response and use
its body shape to fingerprint whether a target account is in deletion
grace. CSRF rejects the forged request first; the lockout only fires for
requests that already passed origin / cookie / CSRF checks.

In ``app.main.create_app`` middleware is added in reverse-execution
order, so we add this middleware BEFORE ``CSRFMiddleware`` (i.e., closer
to the route handler).

Cancel-path resolution (P1-5)
-----------------------------
The cancel route's effective path is resolved at app startup via
``app.url_path_for("post_me_delete_cancel")`` and stashed on
``app.state.deletion_cancel_path``. The previous hard-coded literal
silently went out of sync with FastAPI's actual mounting — a router
prefix change would have made every cancel POST 403 itself. The
startup resolver fails loudly if the route is missing.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth.session_cookie import _decode_cookie_payload, verify_epoch_claim
from app.config import get_settings
from app.observability import deletion_lock_blocked_total

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Fallback path used only when the startup resolver hasn't run yet (e.g.
# unit tests that build a TestClient without going through the lifespan).
# Production runs the lifespan and overrides this via ``app.state``.
_CANCEL_PATH_FALLBACK = "/api/v1/auth/me/delete/cancel"


class DeletionLockMiddleware(BaseHTTPMiddleware):
    """Block mutating requests while the caller's deletion grace is open."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        method = request.method.upper()
        if method not in _UNSAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        cancel_path = getattr(
            request.app.state, "deletion_cancel_path", _CANCEL_PATH_FALLBACK
        )
        if path == cancel_path:
            return await call_next(request)

        settings = get_settings()
        payload = _decode_cookie_payload(request, settings)
        if payload is None:
            return await call_next(request)
        sub = payload.get("sub")
        if not sub:
            return await call_next(request)
        try:
            user_id = uuid.UUID(sub)
        except (TypeError, ValueError):
            return await call_next(request)

        # Resolve the user row via a short-lived async session — we cannot
        # use the request-scoped ``get_db`` dependency from middleware.
        # Imports are local so the middleware module stays import-cheap.
        from app.db.session import AsyncSessionLocal
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()

        if user is None:
            return await call_next(request)
        # If the cookie's epoch claim is stale, fall through — auth will
        # reject it at the route layer, and the lockout body would
        # otherwise leak deletion state to attackers brandishing an
        # already-revoked cookie.
        if not verify_epoch_claim(payload, user):
            return await call_next(request)
        if user.deletion_scheduled_at is None:
            return await call_next(request)

        # One increment per 403. The label carries the rejected path so
        # an operator can see at a glance which endpoint a stuck client
        # is retrying against (a misbehaving FE that keeps POSTing
        # /me/email/change while the user is mid-grace would otherwise
        # be invisible on dashboards).
        deletion_lock_blocked_total.labels(path=path).inc()

        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Your account is scheduled for deletion. Cancel deletion "
                    "first or contact support."
                ),
                "code": "deletion_scheduled",
                "scheduled_for": user.deletion_scheduled_at.isoformat(),
            },
        )
