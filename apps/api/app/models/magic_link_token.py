"""Magic-link email auth token (one-time use).

Two flows share this table:

* ``sign_in`` (default) — the original magic-link login.
* ``email_change`` — P0-6's two-step ``/me/email/change`` → ``/me/email/confirm``
  flow. The link is mailed to the *new* address; the confirm endpoint asserts
  ``purpose == 'email_change'`` and that the token's user owns the row whose
  ``pending_email`` matches the new address.

The two purposes share the same lifecycle (hash storage, single-use,
30-minute expiry, revoke-on-reissue), so we add a discriminator column
rather than a sibling table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import created_at, nullable_ts, uuid_pk

# Purpose enum — keep in lockstep with the CHECK constraint in migration
# 0016. Callers should reference these constants instead of bare strings.
PURPOSE_SIGN_IN: Final = "sign_in"
PURPOSE_EMAIL_CHANGE: Final = "email_change"


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('sign_in','email_change')",
            name="magic_link_tokens_purpose_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = nullable_ts()
    created_at: Mapped[datetime] = created_at()
    purpose: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=PURPOSE_SIGN_IN,
        server_default="sign_in",
    )
    # Phase 4.A.13 — optional same-origin relative path the
    # ``GET /auth/callback`` redirects to after minting the session
    # cookie. NULL means "use the default ``/missions``". The route
    # re-validates against the shared FE-route allowlist on read so a
    # stale value minted under an older allowlist gets sanitised. Bound
    # at 200 chars; the allowlist patterns are all shorter than this.
    next_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
