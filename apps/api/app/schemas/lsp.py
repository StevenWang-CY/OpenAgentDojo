"""Structured wire frames for the LSP WebSocket proxy (P1-3).

The proxy is intentionally byte-faithful for JSON-RPC traffic — bytes flow
through unmodified in both directions so we are not coupled to mid-stream LSP
protocol revisions. The schema here covers ONLY the small set of structured
text frames the server sends *before / outside* a successful language-server
attachment, so the frontend can render a coherent "LSP unavailable" state
instead of a generic socket-closed error.

Today the only such frame is :class:`LSPErrorFrame`. Future additions (e.g.
``lsp_ready`` once Phase 4 stamps a cold-start budget) live here as well so
the FE's discriminated-union narrowing stays in one place.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LSPErrorFrame(BaseModel):
    """Sent as the only text frame before the proxy closes the WS.

    The ``error`` field carries the stable
    :attr:`app.sandbox.lsp.LSPUnavailableError.error_class` discriminator —
    the FE narrows on it to decide between "show retry button" (
    ``binary_not_found``) and "we'll degrade gracefully forever"
    (``unsupported_language``).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["lsp_error"] = "lsp_error"
    error: str = Field(description="Stable error_class discriminator from LSPUnavailableError")
    language: str = Field(description="Requested language (python|typescript|go)")
    detail: str | None = Field(
        default=None,
        description=(
            "Optional human-readable detail. NEVER includes user content; safe to surface in the UI."
        ),
    )
