"""Banned-command guard for ``POST /api/v1/sessions/{id}/commands``.

A small allowlist would be ideal but the workspace intentionally lets users
run arbitrary commands (`pytest`, `pnpm test`, ad-hoc debugging shells, …).
Instead we maintain a denylist of obvious foot-guns and abuse vectors. If a
match is found we return 400, emit a ``validator.flag`` supervision event,
and never forward the request to the handler.

The regex set deliberately favours false positives over silently letting an
abuse slip through — operators can always whitelist a case after review.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

# Pattern set — keep small, readable, and high-signal.
_BANNED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rm\s+-rf\s+/(?:\s|$)"),
    re.compile(r":\(\)\s*\{"),  # classic fork bomb prelude
    re.compile(r"curl[^|]*\|[^|]*(sh|bash)\b"),
    re.compile(r"wget[^|]*\|[^|]*(sh|bash)\b"),
    re.compile(r"\bnc\s+-l\b"),
    re.compile(r"^\s*sudo\s+"),
    re.compile(r"\bmkfs\."),
    re.compile(r"\bdd\s+if=.*of=/dev/"),
    re.compile(r">\s*/dev/sd[a-z]"),
)

_SESSION_COMMANDS_RE = re.compile(r"^/api/v1/sessions/(?P<sid>[0-9a-fA-F-]{36})/commands/?$")

# Hard upper bound on the request body we are willing to buffer in this
# middleware. The route only accepts ``{"command": "..."}`` JSON; anything
# larger is almost certainly an attempt to OOM the API process (P2-1).
_MAX_BODY_BYTES = 1_048_576  # 1 MiB


def _matches_banned(command: str) -> str | None:
    """Return the matched pattern source string or None."""
    for pattern in _BANNED_PATTERNS:
        if pattern.search(command):
            return pattern.pattern
    return None


class BannedCommandsMiddleware(BaseHTTPMiddleware):
    """Intercept POST /sessions/{id}/commands and block dangerous shell strings."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method.upper() != "POST":
            return await call_next(request)

        m = _SESSION_COMMANDS_RE.match(request.url.path)
        if m is None:
            return await call_next(request)

        # Reject oversize bodies BEFORE we buffer them — protects the API
        # from a trivial OOM by a single malicious client (P2-1). We look
        # at the Content-Length header (defence-in-depth: ``request.body``
        # also caps reads at Starlette's own limit, but the header check
        # short-circuits before any bytes are read).
        cl_header = request.headers.get("content-length")
        if cl_header is not None:
            try:
                if int(cl_header) > _MAX_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "request body too large"},
                    )
            except ValueError:
                # Garbage Content-Length — let the downstream handler reject.
                pass

        # Read the body once and re-inject so the downstream handler still sees it.
        try:
            body_bytes = await request.body()
        except Exception as exc:
            # Fail closed — we can't enforce the banned-command guard if we
            # can't see the body. The request must not be forwarded.
            # ``logger.opt(exception=True)`` attaches the traceback to the
            # record (loguru's equivalent of stdlib's ``exc_info=True``) so
            # operators can triage these incidents from the structured log;
            # without it the traceback was thrown away (P1-B7).
            logger.opt(exception=True).warning(
                "[banned_commands] could not read request body: {}",
                exc,
            )
            return JSONResponse(
                status_code=400,
                content={"detail": "could not read request body"},
            )

        async def _replay() -> dict[str, object]:  # ASGI receive
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        request._receive = _replay

        if not body_bytes:
            return await call_next(request)

        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # Fail closed — a malformed body must not bypass the banned-command
            # guard. The downstream handler would also reject it but we
            # short-circuit so the bypass window is zero (P1-B11).
            logger.debug("[banned_commands] body parse failed: {}", exc)
            return JSONResponse(
                status_code=400,
                content={"detail": "invalid JSON body"},
            )

        command = ""
        if isinstance(payload, dict):
            command = str(payload.get("command", ""))

        matched = _matches_banned(command)
        if matched is None:
            return await call_next(request)

        session_id_str = m.group("sid")
        logger.warning(
            "[banned_commands] blocked session={} pattern={!r} cmd={!r}",
            session_id_str,
            matched,
            command[:120],
        )

        # Fire-and-forget supervision event so the timeline still records the attempt.
        try:
            await _emit_flag(uuid.UUID(session_id_str), command, matched)
        except Exception as exc:  # pragma: no cover — never block on telemetry
            logger.debug("validator.flag emit failed: {}", exc)

        return JSONResponse(status_code=400, content={"detail": "banned command"})


async def _emit_flag(session_id: uuid.UUID, command: str, pattern: str) -> None:
    """Record a ``validator.flag`` supervision event for the blocked command."""
    from app.db.session import AsyncSessionLocal
    from app.sessions.events import EventEmitter, get_redis

    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        emitter = EventEmitter(db=db, redis_client=redis)
        await emitter.emit(
            session_id=session_id,
            event_type="validator.flag",
            payload={
                "reason": "banned_command",
                "pattern": pattern,
                "command": command[:500],
            },
        )
        await db.commit()
