"""User-consent records + the account-scoped supervision-event log.

Two append-only ORM tables. :class:`UserConsent` (P0-5) carries the user's
actual opt-in / opt-out decisions (one row per kind+version+action).
:class:`AccountEvent` is the account-scoped supervision-style audit log
covering BOTH the original ``consent.*`` transitions and the P0-6
``account.*`` self-service flows (email change, sign-out-everywhere,
deletion schedule/cancel).

Account-scoped events cannot live on ``supervision_events`` because that
table's ``session_id`` is a NOT NULL FK to ``sessions``, and faking a
sentinel session per user would be load-bearing dead state. Keeping the
two tables side-by-side makes it trivial for future tooling to union both
event streams (replay tools assume the same row shape across both).

The table was originally named ``consent_events`` (P0-5) and was renamed
to ``account_events`` in migration 0017 when P0-6's ``account.*`` events
needed to land somewhere durable; the class alias ``ConsentEvent`` is
kept for source-compat with any external tooling that still imports it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._helpers import uuid_pk

# Single source of truth for what event_type literals the CHECK constraint
# accepts. Keep in lockstep with the CHECK in migration 0017 — appending a
# new literal here REQUIRES a migration that widens the CHECK.
ALLOWED_ACCOUNT_EVENT_TYPES: Final[tuple[str, ...]] = (
    "consent.granted",
    "consent.revoked",
    "account.email_change_requested",
    "account.email_changed",
    "account.signed_out_all_sessions",
    "account.deletion_scheduled",
    "account.deletion_cancelled",
    # Terminal event emitted by the hard-delete worker (P2 bundle). Migration
    # 0018 widens the CHECK to accept this literal — earlier deploys without
    # the migration applied will fail on insert with a CHECK violation, which
    # is the correct failure mode (the worker logs + rolls back).
    "account.deleted",
)


class UserConsent(Base):
    """One row per consent action — strictly append-only.

    The "current" state for a (user, kind) pair is whichever row has the
    largest ``granted_at`` (the GET handler picks the first match under an
    ``ORDER BY granted_at DESC`` projection). There is intentionally no
    UNIQUE on (user_id, kind, version): a user can toggle the same kind
    multiple times within a single policy version (open banner → grant →
    reopen → revoke), and every transition must persist as its own audit
    row. The (user_id, kind, granted_at DESC) index keeps the latest-row
    lookup cheap regardless of history depth.
    """

    __tablename__ = "user_consents"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('analytics','functional','marketing')",
            name="user_consents_kind_check",
        ),
        Index(
            "idx_user_consents_user_kind",
            "user_id",
            "kind",
            "granted_at",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ip_address_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class AccountEvent(Base):
    """Append-only account-scoped supervision events keyed by user.

    Layout intentionally mirrors ``supervision_events`` (id / event_type /
    payload / occurred_at) so a future replayer can union both streams
    without a coercion layer. Carries the original ``consent.*`` events
    (P0-5) alongside the P0-6 ``account.*`` self-service events; the
    CHECK constraint enumerates the full allow-list.
    """

    __tablename__ = "account_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN (" + ",".join(f"'{v}'" for v in ALLOWED_ACCOUNT_EVENT_TYPES) + ")",
            name="account_events_type_check",
        ),
        Index("idx_account_events_user_time", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Source-compat alias — external tooling and older imports continue to
# work under the historical name even after the table rename.
ConsentEvent = AccountEvent
