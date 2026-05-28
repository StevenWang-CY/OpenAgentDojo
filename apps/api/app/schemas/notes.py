"""Pydantic v2 schemas for the per-session scratchpad (P1-4).

Two surfaces live here:

* :class:`SessionNoteRead` / :class:`SessionNoteWrite` — the GET/PUT
  payloads for ``/sessions/{id}/note``.
* :class:`NoteViewedDuringPromptBody` — the POST body for
  ``/sessions/{id}/events/note-viewed`` (the rare event fired by the FE
  when the agent-chat composer is focused while the scratchpad has
  content).

The 32 KB body cap lives on :class:`SessionNoteWrite` (Pydantic's
``StringConstraints`` rejects bodies whose UTF-16 length exceeds 32768).
The router does the *byte-level* re-check before persisting so the
413 envelope carries the canonical ``limit_bytes=32768`` constant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Hard upper bound on scratchpad body length in UTF-8 bytes. Mirrored
# in the Postgres CHECK constraint (migration 0028) and surfaced in
# the 413 envelope as ``limit_bytes``.
NOTE_BODY_MAX_BYTES = 32_768


class SessionNoteRead(BaseModel):
    """Response shape for ``GET /sessions/{id}/note``.

    A missing row returns ``body=""`` and ``updated_at`` set to the
    session's start time — the FE treats an empty body identically to
    a never-written one, so this shape is intentionally non-Optional.
    """

    model_config = ConfigDict(from_attributes=True)

    body: str
    updated_at: datetime


class SessionNoteWrite(BaseModel):
    """Request body for ``PUT /sessions/{id}/note``.

    Pydantic's ``StringConstraints`` rejects bodies whose character
    length exceeds the cap *before* we even reach the router; the
    router re-checks the UTF-8 byte length (the load-bearing limit)
    so a body of e.g. all ASCII chars at the boundary doesn't slip
    through against a high-byte-count payload that happens to have a
    low character count.
    """

    body: Annotated[
        str,
        StringConstraints(max_length=NOTE_BODY_MAX_BYTES),
    ] = Field(
        ...,
        description="Markdown-flavoured scratch text (max 32 KB UTF-8).",
    )


class NoteViewedDuringPromptBody(BaseModel):
    """Request body for ``POST /sessions/{id}/events/note-viewed``.

    The FE captures the scratchpad size at the moment the composer
    receives focus and passes it here so the supervision event's
    payload reflects the user-visible state at view time (not the
    server-stored body, which can drift between debounced writes).
    """

    bytes_at_view: int = Field(
        ...,
        ge=0,
        description="Scratchpad UTF-8 byte length captured by the FE at view time.",
    )
