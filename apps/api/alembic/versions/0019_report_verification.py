"""Report verification artifact (P0-11) — verification hash/signature + report_renders.

Adds the schema P0-11 needs to ship the credentialing artifact:

  * ``submissions.verification_hash`` (TEXT, NULL) — SHA-256 of the
    canonical envelope (see ``app.reports.verification.canonical_json``).
    Nullable because back-fill is best-effort: a graded submission whose
    envelope cannot be re-derived (e.g. its mission folder has been
    removed) cannot be stamped without a separate, offline reconciliation
    step. The verify endpoint returns 404 when the column is NULL.
  * ``submissions.verification_signature`` (TEXT, NULL) — HMAC-SHA256 of
    the hash, signed with ``settings.verify_secret``. Same NULL semantics.
  * ``report_renders`` table — one row per ``(submission_id, kind)`` for
    the PDF + PNG download pipeline. Lifecycle is
    ``queued → running → ready`` (or ``failed``); a force re-render
    flips the row back to ``queued`` and overwrites the s3_key. The
    UNIQUE constraint on ``(submission_id, kind)`` plus the route's
    ``ON CONFLICT`` upsert pattern enforces "one PDF and one PNG per
    submission, latest wins."

We deliberately do NOT backfill in this migration. The envelope shape
requires reading the mission folder, the user row, and the session row
together; doing that in raw SQL is brittle for nested JSONB. Operators
should run ``apps/api/scripts/backfill_verification.py`` after the
migration lands to stamp historical graded submissions. New graded
submissions are stamped automatically by ``app.grading.runner`` at
grade time.

SQLite divergence
-----------------
``gen_random_uuid()`` is Postgres-only; SQLite falls back to a
client-generated UUID via the model layer (``app/models/report_render.py``
default factory). Both branches are exercised by the test harness which
patches the models for SQLite — see ``conftest._patch_models_for_sqlite``.

Revision ID: 0019_report_verification
Revises: 0018_account_deleted_event
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_report_verification"
down_revision: str | None = "0018_account_deleted_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # ---- submissions: verification_hash / verification_signature ----
    # Nullable so the migration is non-destructive across already-graded
    # rows; the runner stamps both fields at grade time for new
    # submissions, and the backfill script handles historical rows.
    op.add_column(
        "submissions",
        sa.Column("verification_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "submissions",
        sa.Column("verification_signature", sa.Text(), nullable=True),
    )

    # ---- report_renders ----
    op.create_table(
        "report_renders",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()") if is_postgres else None,
        ),
        sa.Column(
            "submission_id",
            sa.dialects.postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else None,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('pdf','png')",
            name="report_renders_kind_check",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','ready','failed')",
            name="report_renders_status_check",
        ),
        sa.UniqueConstraint(
            "submission_id",
            "kind",
            name="uq_report_renders_submission_kind",
        ),
    )

    op.create_index(
        "idx_report_renders_status",
        "report_renders",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_report_renders_status", table_name="report_renders")
    op.drop_table("report_renders")
    op.drop_column("submissions", "verification_signature")
    op.drop_column("submissions", "verification_hash")
