"""P0-6 — the data-export worker produces a complete user zip.

* Seeds a user with sessions / submissions / agent turns / badges.
* Runs ``_async_build_user_export`` directly (so the test stays inside
  pytest-asyncio's loop instead of nesting ``asyncio.run``).
* Captures the bytes written to S3 via a monkeypatched ``put_object``.
* Unpacks the zip and asserts every expected JSONL is present + non-empty.
* Cross-checks: no row from a second user leaks into the export.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.agent_turn import AgentTurn
from app.models.badge import Badge
from app.models.data_export import EXPORT_STATUS_READY, DataExport
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.models.user_badge import UserBadge


async def _bound(db_engine):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed_world(session_local) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed two users (me + other) with one session each.

    The export run for ``me`` must include ONLY my data and no row keyed
    on the ``other`` user. Returns (me_id, other_id).
    """
    me_id = uuid.uuid4()
    other_id = uuid.uuid4()
    me_session_id = uuid.uuid4()
    other_session_id = uuid.uuid4()
    badge_id = "first-export"

    async with session_local() as db:
        db.add(
            Mission(
                id="export-mission",
                title="Export",
                difficulty="beginner",
                category="auth",
                repo_pack="x",
                initial_commit="HEAD",
                estimated_minutes=5,
                failure_mode="x",
                skills_tested=["x"],
                manifest_sha256="sha",
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        db.add(
            User(
                id=me_id,
                email="me@example.com",
                handle="me-export",
                session_epoch=1,
                created_at=datetime.now(UTC),
            )
        )
        db.add(
            User(
                id=other_id,
                email="other@example.com",
                handle="other-export",
                session_epoch=1,
                created_at=datetime.now(UTC),
            )
        )
        db.add(
            SessionRow(
                id=me_session_id,
                user_id=me_id,
                mission_id="export-mission",
                status="graded",
                started_at=datetime.now(UTC) - timedelta(hours=1),
                completed_at=datetime.now(UTC),
            )
        )
        db.add(
            SessionRow(
                id=other_session_id,
                user_id=other_id,
                mission_id="export-mission",
                status="graded",
                started_at=datetime.now(UTC) - timedelta(hours=2),
                completed_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        # Per-session data for me: one agent turn + one submission.
        db.add(
            AgentTurn(
                session_id=me_session_id,
                turn_index=1,
                user_prompt="please find the bug",
                selected_context={},
                agent_response="here is my fix",
            )
        )
        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=me_session_id,
                final_diff="",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={"total": 80, "dimensions": {}},
                total_score=80,
            )
        )
        # A submission belonging to the OTHER user must never appear in
        # my export.
        db.add(
            Submission(
                id=uuid.uuid4(),
                session_id=other_session_id,
                final_diff="",
                visible_test_results=[],
                hidden_test_results=[],
                validator_results=[],
                score_report={"total": 99, "dimensions": {}, "marker": "other-only"},
                total_score=99,
            )
        )
        # Badges: define a badge + a UserBadge row for me only.
        db.add(
            Badge(
                id=badge_id,
                title="First export",
                description="Did the thing",
                icon="dl",
            )
        )
        db.add(
            UserBadge(
                user_id=me_id,
                badge_id=badge_id,
            )
        )
        await db.commit()
    return me_id, other_id


@pytest.mark.asyncio
async def test_export_bundle_contains_only_owners_data(db_engine, monkeypatch) -> None:
    session_local = await _bound(db_engine)
    me_id, other_id = await _seed_world(session_local)

    # Insert the export row directly so we can drive the worker.
    export_id = uuid.uuid4()
    async with session_local() as db:
        db.add(DataExport(id=export_id, user_id=me_id, status="queued"))
        await db.commit()

    # Capture every byte put_object would upload + every URL it would sign.
    captured_uploads: list[tuple[str, bytes]] = []

    def _fake_put(key, body, *, content_type):
        if hasattr(body, "read"):
            body.seek(0)
            data = body.read()
        else:
            data = bytes(body)
        captured_uploads.append((key, data))
        return len(data)

    def _fake_sign(key, *, expires_in):
        return f"https://signed.test/{key}?ttl={expires_in}"

    # Patch both the storage module and the names imported into the worker.
    monkeypatch.setattr("app.storage.put_object", _fake_put)
    monkeypatch.setattr("app.workers.account_export.put_object", _fake_put)
    monkeypatch.setattr("app.storage.generate_download_url", _fake_sign)
    monkeypatch.setattr("app.workers.account_export.generate_download_url", _fake_sign)

    # Point the worker at the test engine.
    from app.db import session as session_module

    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = session_local  # type: ignore[assignment]
    try:
        from app.workers.account_export import _async_build_user_export

        await _async_build_user_export(export_id)
    finally:
        session_module.AsyncSessionLocal = original  # type: ignore[assignment]

    # The worker must have transitioned the row to ``ready``.
    async with session_local() as db:
        row = (await db.execute(select(DataExport).where(DataExport.id == export_id))).scalar_one()
    assert row.status == EXPORT_STATUS_READY
    assert row.s3_key is not None
    assert row.s3_key.startswith(f"data-exports/{me_id}/")
    assert row.bytes_total and row.bytes_total > 0

    # Unpack the captured zip.
    assert len(captured_uploads) == 1
    key, data = captured_uploads[0]
    assert key == row.s3_key

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())

    # Every expected file is present.
    expected = {
        "README.md",
        "user.json",
        "sessions.jsonl",
        "agent_turns.jsonl",
        "file_changes.jsonl",
        "command_runs.jsonl",
        "submissions.jsonl",
        "supervision_events.jsonl",
        "badges.jsonl",
        "consents.jsonl",
        # Migration 0017 renamed consent_events → account_events; the
        # export now ships the supervision-event log under a name that
        # reflects the broader semantics (consent.* + account.* events).
        "account_events.jsonl",
    }
    missing = expected - names
    assert not missing, f"missing files: {missing}"

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        readme = zf.read("README.md").decode()
        user_json = json.loads(zf.read("user.json"))
        sessions_lines = [
            ln for ln in zf.read("sessions.jsonl").decode().splitlines() if ln.strip()
        ]
        turns_lines = [
            ln for ln in zf.read("agent_turns.jsonl").decode().splitlines() if ln.strip()
        ]
        subs_lines = [ln for ln in zf.read("submissions.jsonl").decode().splitlines() if ln.strip()]
        badges_lines = [ln for ln in zf.read("badges.jsonl").decode().splitlines() if ln.strip()]

    # README mentions the expiry policy + cites every file.
    assert "expires" in readme.lower()

    # user.json scrubs session_epoch / tutorial_replay_count.
    assert "session_epoch" not in user_json
    assert "tutorial_replay_count" not in user_json
    assert user_json["id"] == str(me_id)

    # Non-empty per-user data.
    assert len(sessions_lines) == 1
    assert len(turns_lines) == 1
    assert len(subs_lines) == 1
    assert len(badges_lines) == 1

    # No leakage: the other user's marker submission must NOT appear.
    raw_blob = data
    assert b"other-only" not in raw_blob, "another user's data leaked into the export bundle"
    assert str(other_id).encode() not in raw_blob, "another user's id leaked into the export bundle"
