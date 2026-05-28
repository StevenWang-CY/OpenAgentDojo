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
    # ``error`` is kept as ``str`` (not the :class:`LSPErrorClass` literal)
    # because pydantic narrows ``Literal`` unions on the wire — a new class
    # added to the source enum but not yet shipped to the FE would 422 the
    # whole frame. The source-of-truth allow-list lives in
    # :data:`app.sandbox.lsp.LSPErrorClass`; new entries to be aware of as of
    # the P1-3 audit:
    #
    # * ``sandbox_busy``     — apply-patch (or another exclusive mutation)
    #   is in flight on this handle; the WS refuses to attach an LSP
    #   mid-mutation. Close code 4503.
    # * ``lsp_oom``          — the LSP process was reaped by the kernel OOM
    #   killer (exit 137 / signal 9). FE can show "memory cap hit, falling
    #   back to syntax-only" and surface a per-language hint.
    # * ``origin_forbidden`` — WS upgrade Origin header was not on the
    #   ``settings.cors_origins`` allow-list. Close code 4403.
    error: str = Field(description="Stable error_class discriminator from LSPUnavailableError")
    language: str = Field(description="Requested language (python|typescript|go)")
    detail: str | None = Field(
        default=None,
        description=(
            "Optional human-readable detail. NEVER includes user content; safe to surface in the UI."
        ),
    )
