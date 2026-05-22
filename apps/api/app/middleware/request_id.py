"""Per-request correlation id middleware.

Each incoming request gets an ``X-Request-ID``: either echoed back from the
caller (so upstream tracing systems can keep their own ids) or freshly
minted with :func:`uuid.uuid4`. The id is:

* stored on ``request.state.request_id`` for handlers to log explicitly,
* bound into ``loguru.logger.contextualize`` so every log line emitted
  while serving the request carries ``extra.request_id`` automatically,
* echoed back on the response via the ``X-Request-ID`` header so clients
  can quote it in bug reports.

Mounted early in :mod:`app.main` (before CSRF/CORS) so even rejected
requests still produce a correlated log line.
"""

from __future__ import annotations

import uuid

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Single header constant — keeps casing identical on read and write.
REQUEST_ID_HEADER = "X-Request-ID"


def _coerce_incoming(value: str | None) -> str:
    """Use the caller-supplied id if it looks safe; otherwise mint a new one.

    We accept any printable id up to 128 chars (alnum + dashes + underscores)
    to keep the wire format permissive without letting attackers smuggle log
    payloads through the header (CR/LF injection, oversized blobs, etc.).
    """
    if not value:
        return uuid.uuid4().hex
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return uuid.uuid4().hex
    # Reject anything that isn't trivially printable identifier-ish — this
    # is intentionally stricter than the spec so a malicious header can't
    # poison structured logs.
    safe = "".join(c if c.isalnum() or c in "-_" else "" for c in candidate)
    return safe or uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request id to ``request.state``, logs, and the response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request_id = _coerce_incoming(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id

        # ``contextualize`` binds extra fields to every log emitted while the
        # block is open — including from background tasks spawned with the
        # same loop context. This is what makes per-request log correlation
        # work without each call site remembering to thread the id through.
        with logger.contextualize(request_id=request_id):
            response: Response = await call_next(request)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


__all__ = ["REQUEST_ID_HEADER", "RequestIdMiddleware"]
