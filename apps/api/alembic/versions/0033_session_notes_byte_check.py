"""P1-6 audit item 9 — swap session_notes body CHECK from chars to bytes.

The original ``session_notes_body_length`` CHECK constraint on
``session_notes.body`` was declared as ``length(body) <= 32768``. On
PostgreSQL ``length()`` counts **characters**, so a payload of 32 768
multi-byte UTF-8 glyphs could weigh up to ~128 KB on disk — four times
the intended budget and well over the 413 guard at the API layer
(``apps/api/app/sessions/notes.py``), which counts UTF-8 bytes.

This migration drops the character-based constraint and re-adds it
with ``octet_length(body) <= 32768`` so the database-side cap matches
the API-layer cap exactly. The ORM model in
``apps/api/app/models/session_note.py`` is updated in the same audit
commit to keep ``Base.metadata.create_all`` (the SQLite test path) in
lockstep.

SQLite divergence
-----------------
SQLite is reached via ``Base.metadata.create_all`` in
``tests/conftest.py`` rather than through Alembic, so this migration
is a no-op under tests. SQLite's ``octet_length`` is available as a
built-in (verified: CREATE accepts it and oversized INSERTs fail with
``CHECK constraint failed``), so the constraint now functions
correctly under both engines.

Migration ordering
------------------
``down_revision = "0032_coaching_cache_user_index"``. The two
constraints share a name and both target the same column, so
``downgrade`` puts the original ``length()`` form back verbatim —
re-running the migration end-to-end leaves the schema bit-identical
to the pre-0033 shape (modulo the constraint text).

Revision ID: 0033_session_notes_byte_check
Revises: 0032_coaching_cache_user_index
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0033_session_notes_byte_check"
down_revision: str | None = "0032_coaching_cache_user_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "session_notes_body_length",
        "session_notes",
        type_="check",
    )
    op.create_check_constraint(
        "session_notes_body_length",
        "session_notes",
        "octet_length(body) <= 32768",
    )


def downgrade() -> None:
    op.drop_constraint(
        "session_notes_body_length",
        "session_notes",
        type_="check",
    )
    op.create_check_constraint(
        "session_notes_body_length",
        "session_notes",
        "length(body) <= 32768",
    )
