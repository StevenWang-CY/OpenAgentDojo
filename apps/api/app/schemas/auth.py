"""Auth-related response schemas.

Kept small and explicit so the public OpenAPI surface stays stable and the
frontend's generated types don't churn on every refactor.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class WsTokenRead(BaseModel):
    """Response payload for ``GET /sessions/{id}/ws-token``."""

    token: str
    ttl_seconds: int


class ShareTokenRead(BaseModel):
    """Response payload for ``POST /reports/{id}/share``."""

    share_token: str
    share_url: str
    expires_at: datetime
