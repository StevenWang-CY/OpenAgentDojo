"""P1-6 replay artefact contract tests.

Covers:
  * Byte determinism across two builds of the same submission
    (the load-bearing invariant — every signed artefact's bytes are
    a pure function of DB state).
  * Signature roundtrip — recomputing the HMAC with the same
    VERIFY_SECRET yields the persisted signature.
  * Privacy-redaction matrix — share-token holders see redacted
    prompt-bearing payloads and no scratchpad body.
  * Endpoint auth matrix — owner / share / anonymous / tutorial /
    non-graded.
  * Zip bundle contents — every required file present, verify.html
    self-contained.
  * ETag stability — derived from ``replay_signature``.
  * Golden fixture guard — the canonical bytes are pinned at
    ``tests/fixtures/replay_canonical.json``; mutating the
    canonicalisation rules flips this test, which is the desired
    early-warning signal.
"""

from __future__ import annotations

import json
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.session_note import SessionNote
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.reports.replay import (
    PROMPT_BEARING_EVENT_TYPES,
    REDACTION_SAFE_EVENT_TYPES,
    canonical_json,
    replay_signature,
)
from app.reports.verification import (
    build_envelope,
    compute_hash,
    compute_signature,
    verify_secret,
)

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "replay_canonical.json"
)

# ---------------------------------------------------------------------------
# Pinned fixture inputs — all timestamps are FIXED so the golden bytes are
# reproducible bit-for-bit across machines. Keep these constants stable;
# bumping them invalidates the fixture and forces a regenerate cycle.
# ---------------------------------------------------------------------------

# Fixed UUIDs with letters in the leading byte so SQLite stores them as
# TEXT rather than misinterpreting an all-numeric hex column as a float.
_FIXED_OWNER_ID = uuid.UUID("a1111111-1111-4111-8111-111111111111")
_FIXED_STRANGER_ID = uuid.UUID("a2222222-2222-4222-8222-222222222222")
_FIXED_SESSION_ID = uuid.UUID("a3333333-3333-4333-8333-333333333333")
_FIXED_SUBMISSION_ID = uuid.UUID("a4444444-4444-4444-8444-444444444444")
_FIXED_GRADED_AT = datetime(2026, 5, 23, 18, 42, 11, tzinfo=UTC)
_FIXED_EVENT_BASE = datetime(2026, 5, 23, 18, 31, 2, 123456, tzinfo=UTC)
_FIXED_EXPORTED_AT = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
_FIXED_NOTE_BODY = "I think the issue is cookie expiration handling."

_FIXTURE_MISSION_ID = "auth-cookie-expiration"


async def _seed(db_engine) -> None:
    """Seed a deterministic submission + session + events + note."""
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
                id=_FIXED_OWNER_ID,
                email="owner@arena.local",
                display_name="Jane Doe",
                handle="jane",
            )
        )
        db.add(
            User(
                id=_FIXED_STRANGER_ID,
                email="stranger@arena.local",
                display_name="Stranger",
                handle="stranger",
            )
        )
        db.add(
            Mission(
                id=_FIXTURE_MISSION_ID,
                title="Expired Session Cookie Still Grants Access",
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
                id=_FIXED_SESSION_ID,
                user_id=_FIXED_OWNER_ID,
                mission_id=_FIXTURE_MISSION_ID,
                status="graded",
                score=78,
                attempt_index=2,
            )
        )
        await db.flush()

        # Compute the canonical verification hash + signature so the
        # build_replay path mirrors a real grader's persisted state.
        from app.config import get_settings

        sub = Submission(
            id=_FIXED_SUBMISSION_ID,
            session_id=_FIXED_SESSION_ID,
            final_diff="diff --git a/src/session.ts b/src/session.ts\n@@ -1 +1 @@\n-old\n+new\n",
            visible_test_results=[],
            hidden_test_results=[],
            validator_results=[],
            score_report={
                "dimensions": {"safety": 6, "verification": 8},
                "effective_max": 100,
                "missed_failure_mode": False,
                "total": 78,
            },
            total_score=78,
            score_cap_reason=None,
            created_at=_FIXED_GRADED_AT,
        )
        db.add(sub)
        await db.flush()

        session_row = await db.get(SessionRow, _FIXED_SESSION_ID)
        user_row = await db.get(User, _FIXED_OWNER_ID)
        mission_row = await db.get(Mission, _FIXTURE_MISSION_ID)
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

        # Seed three events in deterministic time order. The
        # prompt.submitted payload carries a body the redaction path
        # should hide from share-token holders.
        db.add(
            SupervisionEvent(
                session_id=_FIXED_SESSION_ID,
                event_type="session.started",
                payload={"mission_id": _FIXTURE_MISSION_ID},
                occurred_at=_FIXED_EVENT_BASE,
            )
        )
        db.add(
            SupervisionEvent(
                session_id=_FIXED_SESSION_ID,
                event_type="prompt.submitted",
                payload={"prompt": "Please fix the cookie bug.", "chars": 24},
                occurred_at=_FIXED_EVENT_BASE + timedelta(seconds=5),
            )
        )
        db.add(
            SupervisionEvent(
                session_id=_FIXED_SESSION_ID,
                event_type="agent.responded",
                payload={"response": "Sure, here's the patch", "chars": 22},
                occurred_at=_FIXED_EVENT_BASE + timedelta(seconds=6),
            )
        )

        # Owner scratchpad — must be embedded for the owner path,
        # omitted for the share-token path.
        db.add(
            SessionNote(
                session_id=_FIXED_SESSION_ID,
                body=_FIXED_NOTE_BODY,
                updated_at=_FIXED_GRADED_AT,
            )
        )
        await db.commit()


def _make_owner_cookie() -> str:
    from app.auth.session_cookie import _ALGORITHM
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(_FIXED_OWNER_ID),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


async def _build_artefact_with_session(
    db_engine,
    *,
    redact: bool,
    exported_at: datetime | None = None,
) -> dict:
    from app.config import get_settings
    from app.db import session as session_module
    from app.reports.replay import build_replay as _build

    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    secret = verify_secret(get_settings())
    async with session_module.AsyncSessionLocal() as db:
        return await _build(
            db,
            _FIXED_SUBMISSION_ID,
            redact_payloads=redact,
            verify_secret_value=secret,
            exported_at=exported_at or _FIXED_EXPORTED_AT,
        )


# ---------------------------------------------------------------------------
# Determinism + signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_replay_is_deterministic(db_engine) -> None:
    """Two builds with the same DB state and exported_at must be byte-equal."""
    await _seed(db_engine)
    a = await _build_artefact_with_session(db_engine, redact=False)
    b = await _build_artefact_with_session(db_engine, redact=False)
    assert canonical_json(a) == canonical_json(b)
    # ``exported_at`` is the only non-deterministic field — fix it
    # explicitly via the override above and the bytes must match.
    assert a["replay_signature"] == b["replay_signature"]


@pytest.mark.asyncio
async def test_build_replay_signature_excludes_exported_at(db_engine) -> None:
    """Two builds with DIFFERENT exported_at must still share a signature."""
    await _seed(db_engine)
    a = await _build_artefact_with_session(
        db_engine, redact=False, exported_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    b = await _build_artefact_with_session(
        db_engine, redact=False, exported_at=datetime(2099, 12, 31, tzinfo=UTC)
    )
    assert a["exported_at"] != b["exported_at"]
    assert a["replay_signature"] == b["replay_signature"]


@pytest.mark.asyncio
async def test_replay_signature_verifies(db_engine) -> None:
    """Recomputing the HMAC under the same secret must match the persisted bytes."""
    await _seed(db_engine)
    from app.config import get_settings

    a = await _build_artefact_with_session(db_engine, redact=False)
    recomputed = replay_signature(a, verify_secret(get_settings()))
    assert recomputed == a["replay_signature"]
    assert len(recomputed) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_share_token_redacts_prompt_payloads(db_engine) -> None:
    """Share-token holders see prompt + agent payloads replaced with byte-counts."""
    await _seed(db_engine)
    redacted = await _build_artefact_with_session(db_engine, redact=True)
    full = await _build_artefact_with_session(db_engine, redact=False)

    redacted_by_type = {(e["event_type"], e["id"]): e for e in redacted["events"]}
    full_by_type = {(e["event_type"], e["id"]): e for e in full["events"]}

    for (etype, _id), event in redacted_by_type.items():
        if etype in PROMPT_BEARING_EVENT_TYPES:
            assert event["payload"]["redacted"] is True
            assert isinstance(event["payload"]["byte_count"], int)
            assert event["payload"]["byte_count"] > 0
            # The original payload's prompt text is NOT in the redacted form.
            for v in event["payload"].values():
                assert "cookie bug" not in str(v)
        else:
            # Non-prompt events must be byte-equal.
            assert event["payload"] == full_by_type[(etype, _id)]["payload"]


@pytest.mark.asyncio
async def test_share_token_excludes_scratchpad(db_engine) -> None:
    """Share-token holders never see the scratchpad body."""
    await _seed(db_engine)
    redacted = await _build_artefact_with_session(db_engine, redact=True)
    full = await _build_artefact_with_session(db_engine, redact=False)
    assert "scratchpad" not in redacted
    assert full.get("scratchpad", {}).get("body") == _FIXED_NOTE_BODY


# ---------------------------------------------------------------------------
# HTTP endpoint matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_endpoint_owner_returns_full(client, db_engine) -> None:
    await _seed(db_engine)
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _make_owner_cookie())
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.json"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["submission_id"] == str(_FIXED_SUBMISSION_ID)
    assert body["envelope"]["handle"] == "jane"
    # Owner gets unredacted prompt text.
    prompt_event = next(
        e for e in body["events"] if e["event_type"] == "prompt.submitted"
    )
    assert prompt_event["payload"].get("prompt") == "Please fix the cookie bug."
    # Owner also gets the scratchpad body.
    assert body.get("scratchpad", {}).get("body") == _FIXED_NOTE_BODY


@pytest.mark.asyncio
async def test_replay_endpoint_share_token_redacts(client, db_engine) -> None:
    await _seed(db_engine)
    from app.config import get_settings
    from app.reports.router import issue_share_token

    settings = get_settings()
    token, _ = issue_share_token(_FIXED_SUBMISSION_ID, settings)
    client.cookies.clear()
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.json?share={token}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    prompt_event = next(
        e for e in body["events"] if e["event_type"] == "prompt.submitted"
    )
    assert prompt_event["payload"]["redacted"] is True
    assert "scratchpad" not in body


@pytest.mark.asyncio
async def test_replay_endpoint_anonymous_404(client, db_engine) -> None:
    await _seed(db_engine)
    client.cookies.clear()
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.json"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_endpoint_unknown_id_404(client, db_engine) -> None:
    await _seed(db_engine)
    from app.config import get_settings

    client.cookies.set(get_settings().session_cookie_name, _make_owner_cookie())
    resp = await client.get(f"/api/v1/submissions/{uuid.uuid4()}/replay.json")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_endpoint_not_graded_404(client, db_engine) -> None:
    """An active session must 404 on the replay surface."""
    await _seed(db_engine)
    from app.config import get_settings
    from app.db import session as session_module

    # Flip the session to active mid-run.
    async with session_module.AsyncSessionLocal() as db:
        row = await db.get(SessionRow, _FIXED_SESSION_ID)
        row.status = "active"
        await db.commit()

    client.cookies.set(get_settings().session_cookie_name, _make_owner_cookie())
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.json"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_endpoint_tutorial_404(client, db_engine) -> None:
    """A tutorial mission's submission must 404 on the replay surface."""
    await _seed(db_engine)
    from app.config import get_settings
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        mission_row = await db.get(Mission, _FIXTURE_MISSION_ID)
        mission_row.kind = "tutorial"
        await db.commit()

    client.cookies.set(get_settings().session_cookie_name, _make_owner_cookie())
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.json"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_etag_stable(client, db_engine) -> None:
    """ETag header must equal ``W/"replay-{signature}"``."""
    await _seed(db_engine)
    from app.config import get_settings

    client.cookies.set(get_settings().session_cookie_name, _make_owner_cookie())
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.json"
    )
    assert resp.status_code == 200
    etag = resp.headers.get("etag", "")
    body = resp.json()
    assert etag == f'W/"replay-{body["replay_signature"]}"'
    assert "immutable" in resp.headers.get("cache-control", "").lower()


# ---------------------------------------------------------------------------
# ZIP bundle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_zip_contains_required_files(client, db_engine) -> None:
    await _seed(db_engine)
    from app.config import get_settings

    client.cookies.set(get_settings().session_cookie_name, _make_owner_cookie())
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.zip"
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip")
    assert "filename=" in resp.headers.get("content-disposition", "")

    body = resp.content
    with zipfile.ZipFile(BytesIO(body)) as zf:
        names = set(zf.namelist())
    required = {"replay.json", "final.diff", "README.md", "verify.html"}
    missing = required - names
    assert not missing, f"missing files in zip: {missing}"


@pytest.mark.asyncio
async def test_replay_zip_verify_html_is_self_contained(client, db_engine) -> None:
    await _seed(db_engine)
    from app.config import get_settings

    client.cookies.set(get_settings().session_cookie_name, _make_owner_cookie())
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.zip"
    )
    assert resp.status_code == 200
    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        verify_html = zf.read("verify.html").decode("utf-8")

    # No <script> tags. No remote CSS / JS. No http(s):// references in
    # the HTML body (the canonical URL is allowed to surface as content,
    # but only inside <pre>/<dd> text — not as an href= / src= attribute).
    assert "<script" not in verify_html.lower()
    assert not re.search(r"(href|src)\s*=\s*['\"]https?://", verify_html, re.I)
    assert not re.search(r"<link[^>]+rel=['\"]?stylesheet", verify_html, re.I)


@pytest.mark.asyncio
async def test_replay_zip_anonymous_404(client, db_engine) -> None:
    await _seed(db_engine)
    client.cookies.clear()
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.zip"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Golden fixture — regenerates on first run, asserts on subsequent runs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_byte_fixture(db_engine) -> None:
    """Pin the canonical bytes for a known fixture submission.

    If this test fails, the canonicalisation rules changed. Inspect the
    diff, decide whether the change is intentional, then delete the
    fixture file and re-run this test to regenerate it.
    """
    await _seed(db_engine)
    artefact = await _build_artefact_with_session(
        db_engine, redact=False, exported_at=_FIXED_EXPORTED_AT
    )

    # Strip the verification-secret-sensitive fields so the fixture
    # doesn't bind to whatever VERIFY_SECRET the test environment used.
    # The remaining bytes (envelope, events, pointer, score_report,
    # final_diff, scratchpad) are pure DB-state-derived and fully
    # deterministic across CI runners.
    canonical_form = {
        k: v
        for k, v in artefact.items()
        if k
        not in {
            "envelope_signature",
            "replay_signature",
            "exported_at",
        }
    }
    bytes_now = canonical_json(canonical_form)

    if not _FIXTURE_PATH.exists():
        _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_bytes(bytes_now)
        pytest.skip(
            f"regenerated golden fixture at {_FIXTURE_PATH}; re-run test"
        )

    expected = _FIXTURE_PATH.read_bytes()
    if expected != bytes_now:
        # Provide a meaningful diff in the assertion message — load both
        # sides as dicts and let pytest format the structural delta.
        actual_obj = json.loads(bytes_now.decode("utf-8"))
        expected_obj = json.loads(expected.decode("utf-8"))
        assert actual_obj == expected_obj, (
            "canonicalisation drift detected vs apps/api/tests/fixtures/"
            "replay_canonical.json. If intentional, delete the fixture and re-run."
        )
        # If the dict compare somehow agrees but bytes differ — that
        # is a canonicalisation drift the dict compare can't see (e.g.
        # whitespace, key ordering).
        assert bytes_now == expected, "byte-level canonicalisation drift"


# ---------------------------------------------------------------------------
# P1-6 audit item 5 — float canonicalisation invariance
# ---------------------------------------------------------------------------


def test_canonical_json_normalises_ieee754_noise() -> None:
    """``0.1 + 0.2`` and ``0.3`` must produce identical canonical bytes."""
    noisy = {"score_report": {"dim": 0.1 + 0.2}}
    clean = {"score_report": {"dim": 0.3}}
    assert canonical_json(noisy) == canonical_json(clean)


def test_canonical_json_normalises_nested_floats() -> None:
    """Float rounding walks dicts and lists."""
    noisy = {
        "score_report": {
            "dims": [0.1 + 0.2, {"weighted": 0.7 + 0.1}],
        }
    }
    clean = {"score_report": {"dims": [0.3, {"weighted": 0.8}]}}
    assert canonical_json(noisy) == canonical_json(clean)


@pytest.mark.asyncio
async def test_replay_byte_determinism_across_float_noise(db_engine) -> None:
    """Two artefacts with semantically-equal scores must sign identically.

    Concretely: a submission with ``score_report = {"dim": 0.1 + 0.2}``
    must produce the same ``replay_signature`` as one with
    ``score_report = {"dim": 0.3}``. This is the load-bearing invariant
    of audit item 5 — without it the same DB state replays to different
    bytes on different machines.
    """
    from app.db import session as session_module

    await _seed(db_engine)

    async def _build_with_score(report: dict) -> dict:
        session_module.AsyncSessionLocal = async_sessionmaker(
            bind=db_engine, expire_on_commit=False
        )
        async with session_module.AsyncSessionLocal() as db:
            row = await db.get(Submission, _FIXED_SUBMISSION_ID)
            row.score_report = report
            await db.commit()
        return await _build_artefact_with_session(
            db_engine, redact=False, exported_at=_FIXED_EXPORTED_AT
        )

    a = await _build_with_score({"dim": 0.1 + 0.2})
    b = await _build_with_score({"dim": 0.3})
    assert a["replay_signature"] == b["replay_signature"]


# ---------------------------------------------------------------------------
# P1-6 audit item 14 — share-token redaction is fail-closed
# ---------------------------------------------------------------------------


def test_redaction_safe_event_types_covers_known_schema() -> None:
    """Every event_type in event.schema.json must be classified.

    For each known type we assert: either it is in
    :data:`REDACTION_SAFE_EVENT_TYPES` (payload passes through) OR it
    is NOT (payload would be redacted). The test simply iterates the
    schema enum and walks the closed list — it self-updates when a
    new event_type lands because reading the schema is a runtime
    operation, not a hardcoded list.

    Sanity guardrails:
      * ``prompt.submitted`` / ``agent.responded`` MUST be redacted
        (the original prompt-bearing pair).
      * ``command.run`` MUST be redacted (shell strings can leak
        credentials per audit item 14).
      * ``session.started`` MUST be safe (carries only mission ids
        + sandbox enums).
    """
    repo_root = Path(__file__).resolve().parents[4]
    schema_path = repo_root / "docs" / "schemas" / "event.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    enum = set(schema["properties"]["event_type"]["enum"])

    # Per audit item 14 (+ P1 follow-up), these are deliberately omitted
    # from the safe-list because their payloads carry free-form user
    # content OR leak the size / timing dynamics of the user's private
    # scratchpad (``note.edited`` / ``note.viewed_during_prompt``).
    must_be_redacted = {
        "prompt.submitted",
        "agent.responded",
        "command.run",
        "validator.flag",
        "session.errored",
        "submission.failed",
        "note.edited",
        "note.viewed_during_prompt",
    }
    for etype in must_be_redacted:
        assert etype in enum, f"schema drift: {etype} no longer in schema"
        assert etype not in REDACTION_SAFE_EVENT_TYPES, (
            f"{etype} must NOT be in the safe-list — its payload can leak "
            "free-form user/agent text or the scratchpad's size + dynamics"
        )

    # And these MUST be in the safe-list because their payload is
    # strictly structural metadata.
    must_be_safe = {
        "session.started",
        "patch.applied",
        "test.run",
        "tab.blurred",
        "tab.focused",
    }
    for etype in must_be_safe:
        assert etype in enum, f"schema drift: {etype} no longer in schema"
        assert etype in REDACTION_SAFE_EVENT_TYPES, (
            f"{etype} should be in the safe-list — its payload is "
            "structurally PII-free"
        )

    # Every safe-list entry must correspond to a real schema type
    # (catches typos / stale entries after a schema rename).
    for etype in REDACTION_SAFE_EVENT_TYPES:
        assert etype in enum, (
            f"safe-list contains {etype} but schema does not — drift"
        )


@pytest.mark.asyncio
async def test_share_token_redacts_unknown_event_types(db_engine) -> None:
    """An event_type not in the safe-list is redacted (fail-closed)."""
    await _seed(db_engine)

    # Seed a new event whose type is intentionally NOT in the safe-list.
    from app.db import session as session_module

    session_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )
    async with session_module.AsyncSessionLocal() as db:
        db.add(
            SupervisionEvent(
                session_id=_FIXED_SESSION_ID,
                event_type="command.run",
                payload={
                    "command": "curl -H 'Authorization: Bearer SECRET' https://api.x",
                    "category": "other",
                },
                occurred_at=_FIXED_EVENT_BASE + timedelta(seconds=10),
            )
        )
        await db.commit()

    redacted = await _build_artefact_with_session(db_engine, redact=True)
    command_event = next(
        e for e in redacted["events"] if e["event_type"] == "command.run"
    )
    assert command_event["payload"]["redacted"] is True
    assert isinstance(command_event["payload"]["byte_count"], int)
    # Sanity: the credential string never appears anywhere in the bytes.
    assert "SECRET" not in canonical_json(redacted).decode("utf-8")


# ---------------------------------------------------------------------------
# P1-6 audit item 15 — Vary + nosniff headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_json_emits_vary_and_nosniff_headers(client, db_engine) -> None:
    await _seed(db_engine)
    from app.config import get_settings

    client.cookies.set(get_settings().session_cookie_name, _make_owner_cookie())
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.json"
    )
    assert resp.status_code == 200, resp.text
    vary = resp.headers.get("vary", "")
    assert "Authorization" in vary and "Cookie" in vary, vary
    assert resp.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
async def test_replay_zip_emits_vary_and_nosniff_headers(client, db_engine) -> None:
    await _seed(db_engine)
    from app.config import get_settings

    client.cookies.set(get_settings().session_cookie_name, _make_owner_cookie())
    resp = await client.get(
        f"/api/v1/submissions/{_FIXED_SUBMISSION_ID}/replay.zip"
    )
    assert resp.status_code == 200, resp.text
    vary = resp.headers.get("vary", "")
    assert "Authorization" in vary and "Cookie" in vary, vary
    assert resp.headers.get("x-content-type-options") == "nosniff"


# ---------------------------------------------------------------------------
# P1-6 audit item 18 — replay_signature raises RuntimeError on missing secret
# ---------------------------------------------------------------------------


def test_replay_signature_missing_secret_raises_runtime_error() -> None:
    """Empty / non-string secret must raise RuntimeError (matches verify_secret)."""
    with pytest.raises(RuntimeError):
        replay_signature({"foo": "bar"}, "")
    with pytest.raises(RuntimeError):
        replay_signature({"foo": "bar"}, None)  # type: ignore[arg-type]
