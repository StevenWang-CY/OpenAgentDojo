"""Widen ``account_events`` CHECK to allow the terminal ``account.deleted`` literal.

The P2 observability bundle adds a terminal ``account.deleted`` event so
the audit log records the moment a user row is tombstoned alongside the
rest of the account.* stream (P0_DESIGN §0.3 — every user action emits a
typed event). Without this migration the worker would raise a CHECK
constraint violation on insert and the tombstone would never commit.

Migration is symmetric with 0017: drop + re-add the constraint under the
same name, widened by one literal. SQLite still needs the table-recreate
path for ``DROP CONSTRAINT``.

Revision ID: 0018_account_deleted_event
Revises: 0017_account_event_log
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018_account_deleted_event"
down_revision: str | None = "0017_account_event_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED_EVENT_TYPES_NEW: tuple[str, ...] = (
    "consent.granted",
    "consent.revoked",
    "account.email_change_requested",
    "account.email_changed",
    "account.signed_out_all_sessions",
    "account.deletion_scheduled",
    "account.deletion_cancelled",
    "account.deleted",
)


_ALLOWED_EVENT_TYPES_OLD: tuple[str, ...] = (
    "consent.granted",
    "consent.revoked",
    "account.email_change_requested",
    "account.email_changed",
    "account.signed_out_all_sessions",
    "account.deletion_scheduled",
    "account.deletion_cancelled",
)


def _quoted_list(values: tuple[str, ...]) -> str:
    return ",".join(f"'{v}'" for v in values)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute(
            "ALTER TABLE account_events DROP CONSTRAINT account_events_type_check"
        )
        op.execute(
            "ALTER TABLE account_events ADD CONSTRAINT account_events_type_check "
            f"CHECK (event_type IN ({_quoted_list(_ALLOWED_EVENT_TYPES_NEW)}))"
        )
    else:
        with op.batch_alter_table(
            "account_events", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(
                "account_events_type_check", type_="check"
            )
            batch_op.create_check_constraint(
                "account_events_type_check",
                f"event_type IN ({_quoted_list(_ALLOWED_EVENT_TYPES_NEW)})",
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute(
            "ALTER TABLE account_events DROP CONSTRAINT account_events_type_check"
        )
        op.execute(
            "ALTER TABLE account_events ADD CONSTRAINT account_events_type_check "
            f"CHECK (event_type IN ({_quoted_list(_ALLOWED_EVENT_TYPES_OLD)}))"
        )
    else:
        with op.batch_alter_table(
            "account_events", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(
                "account_events_type_check", type_="check"
            )
            batch_op.create_check_constraint(
                "account_events_type_check",
                f"event_type IN ({_quoted_list(_ALLOWED_EVENT_TYPES_OLD)})",
            )
