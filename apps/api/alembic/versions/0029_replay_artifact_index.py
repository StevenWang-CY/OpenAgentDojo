"""P1-6 — supervision_events covering index for the replay artefact.

Provisions the load-bearing index the replay builder
([P1_DESIGN.md §P1-6](../../../P1_DESIGN.md)) reads from when it
streams the ordered event log for a graded submission. The replay
artefact MUST sort events by ``(occurred_at ASC, id ASC)`` — the same
ordering the grader uses — and a multi-column index that already
covers the id tie-break lets the read path execute as an index-only
scan instead of an index-then-heap lookup.

The existing single-column index ``idx_events_session_time`` (declared
on the ORM model in ``app/models/supervision_event.py``) is retained
for the live-tail subscribers that don't need the id column; this new
index is purely additive.

Migration ordering
------------------
``down_revision = "0028_session_notes"``. The chain is
``... 0027 → 0028 (P1-4 notes) → 0029 (P1-6 replay index) → 0030
(LLM cache)``. The 0030 migration was authored in parallel with 0029
and already declares ``down_revision = "0029_replay_artifact_index"``.

SQLite divergence
-----------------
SQLite is reached via ``Base.metadata.create_all`` in
``tests/conftest.py`` (not Alembic), so this migration is a no-op
under tests. The replay-determinism tests sort events in Python with
the same ``(occurred_at, id)`` key tuple, so the absence of the
covering index in SQLite is invisible to the test suite — the order
contract is enforced in code, not relied upon from the planner.

Revision ID: 0029_replay_artifact_index
Revises: 0028_session_notes
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029_replay_artifact_index"
down_revision: str | None = "0028_session_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The covering index. Note the column order: session_id (equality
    # predicate) → occurred_at (range / sort) → id (tie-break and final
    # sort key). Postgres' planner picks this for ``ORDER BY
    # occurred_at, id LIMIT N`` without a separate sort node.
    op.create_index(
        "idx_events_session_time_id",
        "supervision_events",
        ["session_id", "occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_events_session_time_id",
        table_name="supervision_events",
    )
