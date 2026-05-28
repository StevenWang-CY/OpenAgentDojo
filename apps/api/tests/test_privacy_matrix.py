"""P1-4 + P1-6 — consolidated privacy matrix.

One test file that pins the cross-surface privacy posture for the
two LLM-adjacent features in this batch:

* **Replay artefact** (P1-6 — see ``apps/api/app/reports/replay.py``):
    - Owner-fetched replay includes prompt.submitted text and the
      scratchpad body.
    - Share-token-fetched replay redacts prompt.submitted text and
      omits the scratchpad body entirely.
    - Anonymous (no cookie, no share token) replay 404s.

* **Coaching opt-out** (P1-4 — see
  ``apps/api/app/auth/routes.py::post_me_coaching_consent``): with
  ``users.coaching_reflections_enabled = False`` the coaching endpoint
  refuses to forward the scratchpad text and returns
  ``reflection=null``. The endpoint is owned by Wave 2B and may not be
  merged yet; we skip the assertion (rather than fail) when it is
  absent so this file lands ahead of the Wave 2B merge.

The replay assertions overlap with
``apps/api/tests/reports/test_replay.py`` by design — that file owns
the byte-determinism + golden-fixture invariants; this file is the
short, human-readable contract a reviewer can scan to confirm the
privacy matrix in P1_DESIGN §P1-6 is upheld.

The fixture deliberately mirrors the IDs / data in test_replay.py so
the two test files can be read side-by-side — if a future drift
introduces a mismatch, the failure surfaces in both places.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
from app.reports.replay import PROMPT_BEARING_EVENT_TYPES
from app.reports.verification import (
    build_envelope,
    compute_hash,
    compute_signature,
    verify_secret,
)

# Fixed UUIDs whose leading byte is non-numeric so SQLite stores them
# as TEXT rather than misinterpreting an all-hex column as a float.
# Distinct from the ids in tests/reports/test_replay.py so the two
# test files cannot collide when sharing an engine fixture.
_OWNER_ID = uuid.UUID("b1111111-1111-4111-8111-111111111111")
_SESSION_ID = uuid.UUID("b3333333-3333-4333-8333-333333333333")
_SUBMISSION_ID = uuid.UUID("b4444444-4444-4444-8444-444444444444")
_GRADED_AT = datetime(2026, 5, 23, 18, 42, 11, tzinfo=UTC)
_EVENT_BASE = datetime(2026, 5, 23, 18, 31, 2, 123456, tzinfo=UTC)
_NOTE_BODY = "Cookie expiration check is the load-bearing test."
_PROMPT_TEXT = "Please fix the cookie bug."
_MISSION_ID = "auth-cookie-expiration-privacy"
# ``UserRead`` validates ``email`` through Pydantic's ``EmailStr`` which
# rejects reserved TLDs like ``.local``. ``/auth/me`` (used by the
# coaching-consent round-trip test) serialises through UserRead, so we
# need a valid public-suffix domain even for fixture data. The replay
# tests don't hit /auth/me so they can use ``arena.local``; ours can't.
_OWNER_EMAIL = "privacy-owner@example.com"


async def _seed(db_engine) -> None:
    """Seed a graded submission owned by ``_OWNER_ID``."""
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
                email=_OWNER_EMAIL,
                display_name="Jane",
                handle="jane-privacy",
            )
        )
        db.add(
            Mission(
                id=_MISSION_ID,
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
                id=_SESSION_ID,
                user_id=_OWNER_ID,
                mission_id=_MISSION_ID,
                status="graded",
                score=78,
                attempt_index=1,
            )
        )
        await db.flush()

        sub = Submission(
            id=_SUBMISSION_ID,
            session_id=_SESSION_ID,
            final_diff="diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n",
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

        db.add(
            SupervisionEvent(
                session_id=_SESSION_ID,
                event_type="session.started",
                payload={"mission_id": _MISSION_ID},
                occurred_at=_EVENT_BASE,
            )
        )
        db.add(
            SupervisionEvent(
                session_id=_SESSION_ID,
                event_type="prompt.submitted",
                payload={"prompt": _PROMPT_TEXT, "chars": len(_PROMPT_TEXT)},
                occurred_at=_EVENT_BASE + timedelta(seconds=5),
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


def _make_owner_cookie() -> str:
    from app.auth.session_cookie import _ALGORITHM
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(_OWNER_ID),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


# ---------------------------------------------------------------------------
# Replay privacy matrix (P1-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_replay_includes_prompt_text_and_scratchpad(
    client_with_db, db_engine
) -> None:
    """Owner sees verbatim prompt.submitted payload + scratchpad body."""
    await _seed(db_engine)
    from app.config import get_settings

    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_owner_cookie()
    )
    resp = await client_with_db.get(
        f"/api/v1/submissions/{_SUBMISSION_ID}/replay.json"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    prompt_event = next(
        e for e in body["events"] if e["event_type"] == "prompt.submitted"
    )
    assert prompt_event["payload"]["prompt"] == _PROMPT_TEXT
    assert "redacted" not in prompt_event["payload"]

    assert body.get("scratchpad", {}).get("body") == _NOTE_BODY


@pytest.mark.asyncio
async def test_share_token_replay_redacts_prompt_and_omits_scratchpad(
    client_with_db, db_engine
) -> None:
    """Share-token holder gets byte-count markers + no scratchpad."""
    await _seed(db_engine)
    from app.config import get_settings
    from app.reports.router import issue_share_token

    token, _ = issue_share_token(_SUBMISSION_ID, get_settings())
    client_with_db.cookies.clear()
    resp = await client_with_db.get(
        f"/api/v1/submissions/{_SUBMISSION_ID}/replay.json?share={token}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    prompt_event = next(
        e for e in body["events"] if e["event_type"] == "prompt.submitted"
    )
    assert prompt_event["payload"].get("redacted") is True
    assert isinstance(prompt_event["payload"].get("byte_count"), int)
    # The prompt text MUST NOT appear anywhere in the share-token view.
    serialised = resp.text
    assert _PROMPT_TEXT not in serialised

    # Scratchpad is omitted entirely on the share-token path — not even
    # an empty placeholder is allowed.
    assert "scratchpad" not in body
    assert _NOTE_BODY not in serialised

    # Sanity: the prompt-bearing event-type frozenset stays in sync.
    assert "prompt.submitted" in PROMPT_BEARING_EVENT_TYPES


@pytest.mark.asyncio
async def test_anonymous_replay_404(client_with_db, db_engine) -> None:
    """No cookie, no share token → 404 (we leak nothing)."""
    await _seed(db_engine)
    client_with_db.cookies.clear()
    resp = await client_with_db.get(
        f"/api/v1/submissions/{_SUBMISSION_ID}/replay.json"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Coaching opt-out (P1-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coaching_consent_default_is_opt_in(
    client_with_db, db_engine
) -> None:
    """Fresh accounts default to ``coaching_reflections_enabled = True``."""
    await _seed(db_engine)
    from app.config import get_settings

    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_owner_cookie()
    )
    resp = await client_with_db.get("/api/v1/auth/me/coaching-consent")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"coaching_reflections_enabled": True}


_CSRF_TOKEN = "test-csrf-token-coaching-consent-fixed"


def _csrf_kwargs() -> dict[str, object]:
    """Header + cookie double-submit pair the CSRF middleware accepts."""
    return {
        "headers": {"X-CSRF-Token": _CSRF_TOKEN},
        "cookies": {"arena_csrf": _CSRF_TOKEN},
    }


@pytest.mark.asyncio
async def test_coaching_consent_toggle_round_trip(
    client_with_db, db_engine
) -> None:
    """Flip the bit + re-read; the column persists the new value."""
    await _seed(db_engine)
    from app.config import get_settings

    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_owner_cookie()
    )

    off_resp = await client_with_db.post(
        "/api/v1/auth/me/coaching-consent",
        json={"coaching_reflections_enabled": False},
        **_csrf_kwargs(),
    )
    assert off_resp.status_code == 200, off_resp.text
    assert off_resp.json() == {"coaching_reflections_enabled": False}

    confirm = await client_with_db.get("/api/v1/auth/me/coaching-consent")
    assert confirm.json() == {"coaching_reflections_enabled": False}

    on_resp = await client_with_db.post(
        "/api/v1/auth/me/coaching-consent",
        json={"coaching_reflections_enabled": True},
        **_csrf_kwargs(),
    )
    assert on_resp.status_code == 200, on_resp.text
    assert on_resp.json() == {"coaching_reflections_enabled": True}


@pytest.mark.asyncio
async def test_coaching_endpoint_respects_opt_out(
    client_with_db, db_engine
) -> None:
    """When the column is False, /coaching returns ``reflection=null``.

    Wave 2B owns the coaching endpoint itself. If it hasn't merged when
    this test runs, the route is absent and the assertion is skipped
    (the toggle round-trip above still exercises the privacy invariant
    on the column).
    """
    await _seed(db_engine)
    from app.config import get_settings

    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_owner_cookie()
    )

    set_off = await client_with_db.post(
        "/api/v1/auth/me/coaching-consent",
        json={"coaching_reflections_enabled": False},
        **_csrf_kwargs(),
    )
    assert set_off.status_code == 200

    # Probe the coaching endpoint. The Wave 2B contract returns
    # ``{reflection: null, ...}`` for an opted-out user; absence of the
    # route means Wave 2B hasn't merged yet and we skip rather than fail.
    coaching_url = f"/api/v1/submissions/{_SUBMISSION_ID}/coaching"
    resp = await client_with_db.get(coaching_url)
    if resp.status_code == 404:
        pytest.skip(
            "coaching endpoint not yet merged (Wave 2B); the opt-out "
            "column is exercised by the round-trip test above"
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("reflection") is None, body

    set_on = await client_with_db.post(
        "/api/v1/auth/me/coaching-consent",
        json={"coaching_reflections_enabled": True},
        **_csrf_kwargs(),
    )
    assert set_on.status_code == 200
