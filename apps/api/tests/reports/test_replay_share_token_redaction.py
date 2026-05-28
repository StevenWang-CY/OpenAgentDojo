"""P1 — share-token redaction must hide every PII surface.

These tests pin the privacy posture for the share-token branch of the
replay artefact:

  * ``final_diff`` is owner-only — share-token holders see ``None`` and
    a sibling ``final_diff_byte_count`` magnitude only.
  * ``note.edited`` and ``note.viewed_during_prompt`` payloads are
    redacted (their byte counts / timing data leak the scratchpad's
    size + dynamics).
  * Owner views are byte-for-byte unredacted across the same surfaces.

The fixture is intentionally minimal — it focuses on the redaction
matrix rather than reproducing the golden artefact byte-by-byte.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.session_note import SessionNote
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.reports.replay import REDACTION_SAFE_EVENT_TYPES, build_replay
from app.reports.verification import (
    build_envelope,
    compute_hash,
    compute_signature,
    verify_secret,
)

_OWNER_ID = uuid.UUID("c0000001-1111-4111-8111-111111111111")
_SESSION_ID = uuid.UUID("c0000002-2222-4222-8222-222222222222")
_SUBMISSION_ID = uuid.UUID("c0000003-3333-4333-8333-333333333333")
_MISSION_ID = "auth-cookie-expiration"
_GRADED_AT = datetime(2026, 5, 23, 18, 42, 11, tzinfo=UTC)
_EVENT_BASE = datetime(2026, 5, 23, 18, 31, 2, 123456, tzinfo=UTC)
_FINAL_DIFF_BODY = (
    "diff --git a/src/session.ts b/src/session.ts\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)
_NOTE_BODY = "I think the cookie expiry handling is broken."


async def _seed(db_engine) -> None:
    from app.config import get_settings
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_module.AsyncSessionLocal() as db:
        db.add(
            User(
                id=_OWNER_ID,
                email="owner@arena.local",
                display_name="Jane",
                handle="jane-redact",
            )
        )
        db.add(
            Mission(
                id=_MISSION_ID,
                title="Cookie expiry",
                difficulty="intermediate",
                category="auth",
                repo_pack="fullstack-auth-demo",
                initial_commit="abc123de",
                estimated_minutes=10,
                failure_mode="cookie expiry not enforced",
                skills_tested=["auth"],
                manifest_sha256="deadbeef" * 8,
                version=1,
                kind="standard",
                published=True,
                expected_weak_dim="safety",
            )
        )
        db.add(
            SessionRow(
                id=_SESSION_ID,
                user_id=_OWNER_ID,
                mission_id=_MISSION_ID,
                status="graded",
                score=70,
                attempt_index=1,
            )
        )
        await db.flush()
        sub = Submission(
            id=_SUBMISSION_ID,
            session_id=_SESSION_ID,
            final_diff=_FINAL_DIFF_BODY,
            visible_test_results=[],
            hidden_test_results=[],
            validator_results=[],
            score_report={
                "dimensions": {"safety": 5, "verification": 8},
                "effective_max": 100,
                "missed_failure_mode": False,
                "total": 70,
            },
            total_score=70,
            score_cap_reason=None,
            created_at=_GRADED_AT,
        )
        db.add(sub)
        await db.flush()
        session_row = await db.get(SessionRow, _SESSION_ID)
        user_row = await db.get(User, _OWNER_ID)
        mission_row = await db.get(Mission, _MISSION_ID)
        envelope = build_envelope(
            submission=sub,
            session=session_row,
            manifest=None,
            user=user_row,
            mission_row=mission_row,
        )
        secret = verify_secret(get_settings())
        h = compute_hash(envelope)
        sub.verification_hash = h
        sub.verification_signature = compute_signature(h, secret)

        # Seed prompt-bearing + scratchpad-metadata events. The two
        # ``note.*`` types are the load-bearing assertions for this test.
        db.add(
            SupervisionEvent(
                session_id=_SESSION_ID,
                event_type="prompt.submitted",
                payload={"prompt": "Please debug the cookie bug.", "chars": 27},
                occurred_at=_EVENT_BASE + timedelta(seconds=5),
            )
        )
        db.add(
            SupervisionEvent(
                session_id=_SESSION_ID,
                event_type="note.edited",
                payload={
                    "bytes": 312,
                    "lines": 4,
                    "seconds_since": 64,
                },
                occurred_at=_EVENT_BASE + timedelta(seconds=120),
            )
        )
        db.add(
            SupervisionEvent(
                session_id=_SESSION_ID,
                event_type="note.viewed_during_prompt",
                payload={"bytes_at_view": 312, "seconds_since": 5},
                occurred_at=_EVENT_BASE + timedelta(seconds=125),
            )
        )
        db.add(
            SessionNote(
                session_id=_SESSION_ID,
                body=_NOTE_BODY,
                updated_at=_GRADED_AT,
            )
        )
        await db.commit()


async def _build(db_engine, *, redact: bool):
    from app.config import get_settings
    from app.db import session as session_module

    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    secret = verify_secret(get_settings())
    async with session_module.AsyncSessionLocal() as db:
        return await build_replay(
            db,
            _SUBMISSION_ID,
            redact_payloads=redact,
            verify_secret_value=secret,
            exported_at=datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
        )


def test_note_event_types_are_NOT_in_safe_list() -> None:
    """Both note.* types must be redacted for share-token holders.

    Bare unit test (no async / DB needed): mutating the safe list to
    include these types would silently re-enable the leak.
    """
    assert "note.edited" not in REDACTION_SAFE_EVENT_TYPES
    assert "note.viewed_during_prompt" not in REDACTION_SAFE_EVENT_TYPES


@pytest.mark.asyncio
async def test_share_token_redacts_final_diff(db_engine) -> None:
    """Share-token holders never see the verbatim ``final_diff``."""
    await _seed(db_engine)
    redacted = await _build(db_engine, redact=True)
    full = await _build(db_engine, redact=False)

    # Share-token view: final_diff is None + redacted flag + byte count.
    assert redacted["final_diff"] is None
    assert redacted["final_diff_redacted"] is True
    assert redacted["final_diff_byte_count"] == len(
        _FINAL_DIFF_BODY.encode("utf-8")
    )
    # Owner view: verbatim diff is present + flag is False.
    assert full["final_diff"] == _FINAL_DIFF_BODY
    assert full["final_diff_redacted"] is False
    assert full["final_diff_byte_count"] == len(
        _FINAL_DIFF_BODY.encode("utf-8")
    )


@pytest.mark.asyncio
async def test_share_token_redacts_note_event_payloads(db_engine) -> None:
    """``note.edited`` + ``note.viewed_during_prompt`` payloads must be redacted."""
    await _seed(db_engine)
    redacted = await _build(db_engine, redact=True)

    by_type = {e["event_type"]: e for e in redacted["events"]}
    for etype in ("note.edited", "note.viewed_during_prompt"):
        payload = by_type[etype]["payload"]
        assert payload["redacted"] is True
        assert isinstance(payload["byte_count"], int)
        assert payload["byte_count"] > 0
        # Pre-redaction byte_count signal MUST NOT appear verbatim.
        for key in ("bytes", "lines", "seconds_since", "bytes_at_view"):
            assert key not in payload


@pytest.mark.asyncio
async def test_owner_view_keeps_note_payloads_verbatim(db_engine) -> None:
    """Owner view must surface scratchpad-metadata payloads verbatim."""
    await _seed(db_engine)
    full = await _build(db_engine, redact=False)
    by_type = {e["event_type"]: e for e in full["events"]}

    edited = by_type["note.edited"]["payload"]
    viewed = by_type["note.viewed_during_prompt"]["payload"]
    assert edited == {"bytes": 312, "lines": 4, "seconds_since": 64}
    assert viewed == {"bytes_at_view": 312, "seconds_since": 5}


@pytest.mark.asyncio
async def test_share_token_artefact_bytes_have_no_diff_or_note_content(
    db_engine,
) -> None:
    """Sanity: the redacted artefact's canonical bytes don't contain the diff or note body."""
    from app.reports.replay import canonical_json

    await _seed(db_engine)
    redacted = await _build(db_engine, redact=True)
    raw = canonical_json(redacted).decode("utf-8")
    assert "src/session.ts" not in raw
    assert _NOTE_BODY not in raw
