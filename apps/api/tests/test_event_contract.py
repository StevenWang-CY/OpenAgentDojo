"""Event payload contract conformance (P0-B1).

The FE contract is locked — every supervision event must use the canonical
field names. These tests pin the wire shapes so a refactor that drops or
renames a field surfaces immediately.
"""

from __future__ import annotations

import json
import pathlib
import uuid

import jsonschema
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.supervision_event import SupervisionEvent
from app.sessions.events import _PENDING_KEY, EventEmitter


async def _setup(db_engine) -> uuid.UUID:
    """Seed a session row and return its id."""
    from app.models.mission import Mission
    from app.models.session import SessionRow
    from app.models.user import User

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    sid = uuid.uuid4()
    async with factory() as db:
        db.add(
            User(
                id=user_id,
                email=f"u-{user_id}@arena.local",
                handle=f"u{str(user_id)[:6]}",
            )
        )
        db.add(
            Mission(
                id="event-contract-mission",
                title="Event Contract",
                difficulty="beginner",
                category="testing",
                repo_pack="pack",
                initial_commit="abc",
                estimated_minutes=5,
                failure_mode="none",
                skills_tested=[],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
            )
        )
        db.add(
            SessionRow(
                id=sid,
                user_id=user_id,
                mission_id="event-contract-mission",
                status="active",
            )
        )
        await db.commit()
    return sid


@pytest.mark.asyncio
async def test_session_errored_payload_shape(db_engine) -> None:
    """``session.errored`` carries ``{stage, detail}`` (P0-B1)."""
    sid = await _setup(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        await emitter.emit(
            session_id=sid,
            event_type="session.errored",
            payload={"stage": "sandbox", "detail": "docker daemon unreachable"},
            publish_after_commit=False,
        )
        await db.commit()

    async with factory() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(SupervisionEvent.event_type == "session.errored")
            )
        ).scalar_one()
        assert row.payload == {
            "stage": "sandbox",
            "detail": "docker daemon unreachable",
        }


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_EVENT_SCHEMA = json.loads(
    (_REPO_ROOT / "docs" / "schemas" / "event.schema.json").read_text(encoding="utf-8")
)


@pytest.mark.asyncio
async def test_session_reset_payload_shape(db_engine) -> None:
    """``session.reset`` carries ``{files_discarded, had_agent_patch, seconds_into_session}``.

    P0-12 — the FE timeline tile + the grading diagnostics reader both
    key off these exact field names. A rename or a typo here would
    silently degrade the post-mortem walkthrough, so we pin both the
    persisted row shape AND the JSON-schema acceptance.
    """
    sid = await _setup(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    payload = {
        "files_discarded": 3,
        "had_agent_patch": True,
        "seconds_into_session": 42,
    }
    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        await emitter.emit(
            session_id=sid,
            event_type="session.reset",
            payload=payload,
            publish_after_commit=False,
        )
        await db.commit()

    async with factory() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(SupervisionEvent.event_type == "session.reset")
            )
        ).scalar_one()
        assert set(row.payload.keys()) == {
            "files_discarded",
            "had_agent_patch",
            "seconds_into_session",
        }
        assert row.payload == payload

    # JSON-schema acceptance — guard against a drift between
    # ``docs/schemas/event.schema.json`` and the wire shape above.
    jsonschema.validate(
        instance={
            "session_id": str(sid),
            "event_type": "session.reset",
            "payload": payload,
            "occurred_at": "2026-05-25T00:00:00Z",
        },
        schema=_EVENT_SCHEMA,
    )


@pytest.mark.asyncio
async def test_session_abandoned_payload_shape(db_engine) -> None:
    """``session.abandoned`` carries ``{reason}`` (P0-B1)."""
    sid = await _setup(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        await emitter.emit(
            session_id=sid,
            event_type="session.abandoned",
            payload={"reason": "orphan_sweep"},
            publish_after_commit=False,
        )
        await db.commit()

    async with factory() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(SupervisionEvent.event_type == "session.abandoned")
            )
        ).scalar_one()
        assert row.payload == {"reason": "orphan_sweep"}


@pytest.mark.asyncio
async def test_prompt_submitted_uses_text_and_char_count(db_engine) -> None:
    """`prompt.submitted` payload uses `text` + `char_count` (renamed)."""
    sid = await _setup(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    payload_text = "Investigate the cookie expiration regression."
    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        await emitter.emit(
            session_id=sid,
            event_type="prompt.submitted",
            payload={
                "turn_index": 0,
                "text": payload_text,
                "char_count": len(payload_text),
            },
            publish_after_commit=False,
        )
        await db.commit()

    async with factory() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(SupervisionEvent.event_type == "prompt.submitted")
            )
        ).scalar_one()
        assert row.payload["text"] == payload_text
        assert row.payload["char_count"] == len(payload_text)
        assert row.payload["turn_index"] == 0
        # The legacy `prompt` key MUST be gone — FE doesn't read it.
        assert "prompt" not in row.payload


@pytest.mark.asyncio
async def test_patch_applied_file_count_is_int(db_engine) -> None:
    """`patch.applied` reports ``file_count`` (int) — renamed from ``files_changed``.

    The historical name collided with :attr:`PatchResult.files_changed`
    (which is a *list* of paths), making it ambiguous what callers were
    reading. P1-B10 renames the count field to ``file_count`` so the wire
    schema is self-describing. The legacy ``files_changed`` key MUST NOT
    appear in newly emitted payloads.
    """
    sid = await _setup(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        await emitter.emit(
            session_id=sid,
            event_type="patch.applied",
            payload={
                "turn_index": 1,
                "file_count": 3,
                "added": 12,
                "removed": 4,
            },
            publish_after_commit=False,
        )
        await db.commit()

    async with factory() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(SupervisionEvent.event_type == "patch.applied")
            )
        ).scalar_one()
        assert isinstance(row.payload["file_count"], int)
        assert row.payload["file_count"] == 3
        assert row.payload["added"] == 12
        assert row.payload["removed"] == 4
        # Legacy keys must NOT appear in the new contract.
        assert "files_changed" not in row.payload
        assert "added_lines" not in row.payload
        assert "removed_lines" not in row.payload


# Hand-maintained mirror of ``packages/shared-types/src/events.ts``'s
# ``SupervisionEventType`` enum. Adding a new ``event_type=`` string to the
# emitter REQUIRES a matching entry on the FE — appending to this set
# without also adding it to the TS enum will fail
# ``test_event_types_emitted_in_be_match_fe_enum`` and the FE-side
# ``contract-event-types.test.ts`` will fail in lockstep.
_FE_KNOWN_EVENT_TYPES: set[str] = {
    "session.started",
    "session.errored",
    "session.abandoned",
    "session.provision_failed",
    "context.selected",
    "prompt.submitted",
    "agent.responded",
    "patch.proposed",
    "patch.applied",
    "patch.failed",
    "diff.opened",
    "diff.hovered",
    "file.edited",
    "file.reverted",
    "command.run",
    "test.run",
    "validator.flag",
    "submission.requested",
    "submission.graded",
    "submission.failed",
    # P0-1 tutorial milestones.
    "tutorial.step_completed",
    "tutorial.dismissed",
    "tutorial.completed",
    # P0-4 give-up signal.
    "session.gave_up",
    # P0-12 — workspace reset to mission initial commit.
    "session.reset",
    # P0-5 / P0-6 consent + account transitions (account-scoped; persist to
    # account_events, not supervision_events — see app/models/user_consent.py).
    # The ``account.*`` literals are passed positionally to the route's
    # _build_account_event helper rather than as ``event_type="..."``
    # kwargs, so the grep below does NOT pick them up; we still list
    # them here for documentation of the FE/BE wire contract.
    "consent.granted",
    "consent.revoked",
    # P0-6 account self-service + terminal deletion (account-scoped).
    "account.email_change_requested",
    "account.email_changed",
    "account.signed_out_all_sessions",
    "account.deletion_scheduled",
    "account.deletion_cancelled",
    "account.deleted",
}


def test_event_types_emitted_in_be_match_fe_enum() -> None:
    """Every ``event_type=`` literal emitted by the BE must be in the FE enum.

    Grep is intentional — we want to catch *all* call sites, including ad-hoc
    middleware and worker emits, not just the ones routed through a typed
    helper. The grep target is the ``event_type="..."`` literal that the
    :class:`EventEmitter.emit` API takes as its sole categorical argument.

    A failing assertion here means one of two things:
      1. A new event was added to the BE without updating
         ``packages/shared-types/src/events.ts``. Add it to the enum AND to
         ``_FE_KNOWN_EVENT_TYPES`` above.
      2. A typo crept into an ``event_type=`` literal — fix the typo at the
         call site.
    """
    import re
    from pathlib import Path

    app_root = Path(__file__).resolve().parent.parent / "app"
    pattern = re.compile(r'event_type\s*=\s*"([^"]+)"')

    emitted: set[str] = set()
    for py in app_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for match in pattern.findall(text):
            # Skip the parameter declaration in ``def emit(...)`` itself —
            # the regex matches ``event_type=event_type`` and Prometheus
            # ``labels(event_type=event_type)``; both forward an existing
            # variable rather than introducing a new literal.
            if match in {"event_type", ""}:
                continue
            emitted.add(match)

    missing_from_fe = emitted - _FE_KNOWN_EVENT_TYPES
    assert not missing_from_fe, (
        f"BE emits event_type literals not in the FE enum: "
        f"{sorted(missing_from_fe)}. Update "
        f"packages/shared-types/src/events.ts AND _FE_KNOWN_EVENT_TYPES."
    )


@pytest.mark.asyncio
async def test_publish_deferred_until_after_commit(db_engine) -> None:
    """`EventEmitter.emit` should queue the publish, not fan it out inline."""
    sid = await _setup(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async with factory() as db:
        emitter = EventEmitter(db=db, redis_client=None)
        await emitter.emit(
            session_id=sid,
            event_type="file.edited",
            payload={"path": "src/x.ts", "added": 1, "removed": 0, "source": "user"},
        )
        # Pending queue should now have exactly one entry — the publish was deferred.
        pending = db.info.get(_PENDING_KEY, [])
        assert len(pending) == 1
        channel, message = pending[0]
        assert channel == f"events:session:{sid}"
        assert '"event_type": "file.edited"' in message
