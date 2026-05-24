"""Promote ``consent_events`` to ``account_events`` (P0-6 durable account log).

The P0-5 ``consent_events`` table was always going to grow into the
account-scoped supervision-event stream — it lives next to the user (not
a session), the row layout already mirrors ``supervision_events``, and the
only CHECK-constrained difference between "consent" and "account" events
is the literal in ``event_type``. P0-6 originally side-stepped this by
routing ``account.*`` events through loguru only, which broke the
P0_DESIGN §0.3 invariant ("Every new user action emits a typed event so
the replay tool sees the same data"). We promote the table now so:

  * The five P0-6 ``account.*`` events persist to a queryable log instead
    of evaporating into a structured-log line.
  * The existing two ``consent.*`` events keep landing on the same table
    (no data migration needed — just a rename + widened CHECK).
  * Future ``account.*`` flows (subscription, billing, MFA, ...) have one
    obvious place to write to.

The CHECK is replaced (not extended) so the constraint name carries the
new ``account_events_type_check`` identity — old tooling that joined on
the constraint name will surface the migration via a clean error rather
than a silent semantic drift.

SQLite caveat
-------------
``op.rename_table`` is honoured by both Postgres and SQLite. Dropping a
CHECK constraint on SQLite requires the table-recreate path; we therefore
issue a batch operation on SQLite. The test harness creates fresh tables
from ORM metadata (``conftest._patch_models_for_sqlite``) and never runs
this migration, so the SQLite branch is exercised only by the upgrade
script if a dev ever points it at a SQLite DB.

Revision ID: 0017_account_event_log
Revises: 0016_account_self_service
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017_account_event_log"
down_revision: str | None = "0016_account_self_service"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED_EVENT_TYPES: tuple[str, ...] = (
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

    # Rename the table itself — the same physical rows just live under a
    # broader semantic umbrella. Postgres keeps the constraint + index name
    # references attached to the renamed table; SQLite likewise rebinds
    # them under the new table name.
    op.rename_table("consent_events", "account_events")
    op.execute(
        "ALTER INDEX idx_consent_events_user_time RENAME TO idx_account_events_user_time"
        if is_postgres
        else "DROP INDEX IF EXISTS idx_consent_events_user_time"
    )
    if not is_postgres:
        # SQLite has no ALTER INDEX RENAME — re-create the index under the
        # new name. The table rename above propagates to subsequent DDL.
        op.create_index(
            "idx_account_events_user_time",
            "account_events",
            ["user_id", "occurred_at"],
        )

    # Swap the CHECK so the widened domain covers the P0-6 account.* events
    # alongside the existing consent.* literals. We drop the old constraint
    # by name and re-add under the new name so future tooling that joins
    # on the constraint name sees the new identity.
    if is_postgres:
        op.execute(
            "ALTER TABLE account_events DROP CONSTRAINT consent_events_type_check"
        )
        op.execute(
            "ALTER TABLE account_events ADD CONSTRAINT account_events_type_check "
            f"CHECK (event_type IN ({_quoted_list(_ALLOWED_EVENT_TYPES)}))"
        )
    else:
        # SQLite cannot DROP CONSTRAINT in place — recreate the table via
        # batch_alter_table so the old CHECK is dropped and the new one
        # added in a single migration step.
        with op.batch_alter_table(
            "account_events", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(
                "consent_events_type_check", type_="check"
            )
            batch_op.create_check_constraint(
                "account_events_type_check",
                f"event_type IN ({_quoted_list(_ALLOWED_EVENT_TYPES)})",
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute(
            "ALTER TABLE account_events DROP CONSTRAINT account_events_type_check"
        )
        op.execute(
            "ALTER TABLE account_events ADD CONSTRAINT consent_events_type_check "
            "CHECK (event_type IN ('consent.granted','consent.revoked'))"
        )
        op.execute(
            "ALTER INDEX idx_account_events_user_time RENAME TO idx_consent_events_user_time"
        )
    else:
        with op.batch_alter_table(
            "account_events", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(
                "account_events_type_check", type_="check"
            )
            batch_op.create_check_constraint(
                "consent_events_type_check",
                "event_type IN ('consent.granted','consent.revoked')",
            )
        op.drop_index(
            "idx_account_events_user_time", table_name="account_events"
        )
        op.create_index(
            "idx_consent_events_user_time",
            "account_events",
            ["user_id", "occurred_at"],
        )

    op.rename_table("account_events", "consent_events")
