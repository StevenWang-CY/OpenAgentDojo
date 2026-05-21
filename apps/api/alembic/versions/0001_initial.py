"""Initial schema — all M1 tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Required extensions.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # --- users ---
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("github_login", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- missions ---
    op.create_table(
        "missions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("repo_pack", sa.Text(), nullable=False),
        sa.Column("initial_commit", sa.String(length=64), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("failure_mode", sa.Text(), nullable=False),
        sa.Column(
            "skills_tested",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "published",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.CheckConstraint(
            "difficulty IN ('beginner','intermediate','advanced')",
            name="missions_difficulty_check",
        ),
    )

    # --- sessions ---
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "mission_id",
            sa.Text(),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sandbox_id", sa.Text(), nullable=True),
        sa.Column("current_commit", sa.String(length=64), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column(
            "agent_turns",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.CheckConstraint(
            "status IN ('provisioning','active','submitting','graded','abandoned','error')",
            name="sessions_status_check",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score BETWEEN 0 AND 100)",
            name="sessions_score_range",
        ),
    )
    op.create_index(
        "idx_sessions_user", "sessions", ["user_id", "started_at"]
    )

    # --- agent_turns ---
    op.create_table(
        "agent_turns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("user_prompt", sa.Text(), nullable=False),
        sa.Column("selected_context", postgresql.JSONB(), nullable=False),
        sa.Column("agent_response", sa.Text(), nullable=False),
        sa.Column("applied_patch", sa.Text(), nullable=True),
        sa.Column("patch_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "session_id", "turn_index", name="agent_turns_session_turn_uq"
        ),
    )

    # --- file_changes ---
    op.create_table(
        "file_changes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("hunk_count", sa.Integer(), nullable=False),
        sa.Column("added_lines", sa.Integer(), nullable=False),
        sa.Column("removed_lines", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('agent','user','revert')",
            name="file_changes_source_check",
        ),
    )

    # --- command_runs ---
    op.create_table(
        "command_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("stdout_s3_key", sa.Text(), nullable=True),
        sa.Column("stderr_s3_key", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- submissions ---
    op.create_table(
        "submissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("final_diff", sa.Text(), nullable=False),
        sa.Column("visible_test_results", postgresql.JSONB(), nullable=False),
        sa.Column("hidden_test_results", postgresql.JSONB(), nullable=False),
        sa.Column("validator_results", postgresql.JSONB(), nullable=False),
        sa.Column("score_report", postgresql.JSONB(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- badges ---
    op.create_table(
        "badges",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=False),
    )

    # --- user_badges ---
    op.create_table(
        "user_badges",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "badge_id",
            sa.Text(),
            sa.ForeignKey("badges.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "earned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # --- supervision_events ---
    op.create_table(
        "supervision_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
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
    )
    op.create_index(
        "idx_events_session_time",
        "supervision_events",
        ["session_id", "occurred_at"],
    )

    # --- magic_link_tokens (plan §16) ---
    op.create_table(
        "magic_link_tokens",
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
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("magic_link_tokens")
    op.drop_index("idx_events_session_time", table_name="supervision_events")
    op.drop_table("supervision_events")
    op.drop_table("user_badges")
    op.drop_table("badges")
    op.drop_table("submissions")
    op.drop_table("command_runs")
    op.drop_table("file_changes")
    op.drop_table("agent_turns")
    op.drop_index("idx_sessions_user", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("missions")
    op.drop_table("users")
    # Keep extensions installed — other apps in the database may rely on them.
