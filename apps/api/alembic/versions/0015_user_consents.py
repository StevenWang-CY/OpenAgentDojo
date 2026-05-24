"""User consent records + consent event log (P0-5 cookie / privacy consent).

Adds two append-only tables that together satisfy the GDPR / CCPA / LGPD
audit requirements for opt-in tracking and policy-change reconfirmation:

  * ``user_consents`` — one row per consent action. The latest row by
    ``granted_at`` for each (user, kind) pair is the user's current state.
    The table is strictly append-only — no UPDATE, no DELETE — so a
    regulator can replay the full consent history (every toggle the user
    made, with timestamp, IP hash, and UA). Append-only means we can NOT
    add a UNIQUE on (user_id, kind, version): users can flip the same
    kind multiple times within one policy version (open the banner →
    accept → reopen → revoke) and every action must persist as its own
    audit row.

  * ``consent_events`` — supervision-style event log dedicated to consent
    transitions (``consent.granted`` / ``consent.revoked``). The platform's
    main ``supervision_events`` table has ``session_id`` as a NOT NULL FK
    to ``sessions``, so account-scoped consent events cannot live there
    without a sentinel session per user (which would be load-bearing dead
    state). A separate table keyed by ``user_id`` is the simpler correct
    fit and keeps the supervision-event invariant ("every row belongs to
    a session") intact. The row layout mirrors ``supervision_events`` so
    a future replayer can union them with minimal coercion.

Forward-only safe. The CASCADE on ``user_id`` is required by P0_DESIGN §5
("Deletion request (P0-6): All user_consents rows cascade-delete; the
user's consent history is destroyed along with their account.").

Revision ID: 0015_user_consents
Revises: 0014_give_up
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_user_consents"
down_revision: Union[str, None] = "0014_give_up"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_consents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ip_address_hash", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('analytics','functional','marketing')",
            name="user_consents_kind_check",
        ),
    )
    op.create_index(
        "idx_user_consents_user_kind",
        "user_consents",
        ["user_id", "kind", sa.text("granted_at DESC")],
    )

    # Consent supervision events. Schema mirrors ``supervision_events`` so
    # a future cross-stream replayer can union both tables with minimal
    # coercion; keyed by ``user_id`` because consent decisions are
    # account-scoped, not session-scoped.
    op.create_table(
        "consent_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type IN ('consent.granted','consent.revoked')",
            name="consent_events_type_check",
        ),
    )
    op.create_index(
        "idx_consent_events_user_time",
        "consent_events",
        ["user_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_consent_events_user_time", table_name="consent_events")
    op.drop_table("consent_events")
    op.drop_index("idx_user_consents_user_kind", table_name="user_consents")
    op.drop_table("user_consents")
