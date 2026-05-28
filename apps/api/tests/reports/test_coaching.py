"""P1-4 (§"Coaching reflection in the post-mortem") — contract tests.

Covers the four invariants of the coaching surface:

  * **Opt-out is server-authoritative** — the function short-circuits
    before touching the LLM when ``users.coaching_reflections_enabled``
    is False, AND the endpoint returns 200 with ``reflection=null`` so
    the FE hides the section without an error toast.
  * **Empty notes are a no-op** — no notes, no coaching.
  * **Cache key is keyed on notes content** — identical notes hash
    identically; a one-character edit busts the cache.
  * **Endpoint is owner-only** — anon 401, share-token 403, non-owner
    403; owner 200; LLM down with no cache → 503
    ``{code: "llm_unavailable"}``.
  * **Cache survives the round-trip** — the second call returns
    ``cache_hit=True`` without re-invoking the generator.
  * **Account deletion wipes the user's coaching rows** via the
    ``coaching_cache_user_index`` JOIN — the deletion worker runs and
    the cache row + index row are gone.

Every test stubs the AnthropicClient by monkeypatching
``app.reports.coaching._build_client`` to return a deterministic fake
whose ``messages_create`` either returns a fixed string or raises. We
NEVER call real Bedrock.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.llm import canonical_content_hash
from app.models.coaching_cache_user_index import CoachingCacheUserIndex
from app.models.llm_cache import LLMCache
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.session_note import SessionNote
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.reports import coaching as coaching_module
from app.reports.coaching import CoachingOutcome, generate_coaching_reflection

# ---------------------------------------------------------------------------
# Fixed UUIDs / fixtures
# ---------------------------------------------------------------------------

_OWNER_ID = uuid.UUID("c1111111-1111-4111-8111-111111111111")
_STRANGER_ID = uuid.UUID("c2222222-2222-4222-8222-222222222222")
_SESSION_ID = uuid.UUID("c3333333-3333-4333-8333-333333333333")
_SUBMISSION_ID = uuid.UUID("c4444444-4444-4444-8444-444444444444")
_GRADED_AT = datetime(2026, 5, 23, 18, 42, 11, tzinfo=UTC)
_EVENT_BASE = datetime(2026, 5, 23, 18, 31, 2, 123456, tzinfo=UTC)
_NOTE_BODY = (
    "I think the issue is around cookie expiration handling — the test "
    "in cookie.test.ts is probably what's catching the bug."
)
_OWNER_EMAIL = "coaching-owner@example.com"
_MISSION_ID = "coaching-cookie-expiration"


async def _seed(
    db_engine,
    *,
    coaching_enabled: bool = True,
    note_body: str | None = _NOTE_BODY,
) -> None:
    """Seed a graded submission owned by ``_OWNER_ID``."""
    from app.config import get_settings
    from app.db import session as session_module
    from app.reports.verification import (
        build_envelope,
        compute_hash,
        compute_signature,
        verify_secret,
    )

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
                handle="jane-coaching",
                coaching_reflections_enabled=coaching_enabled,
            )
        )
        db.add(
            User(
                id=_STRANGER_ID,
                email="coaching-stranger@example.com",
                display_name="Stranger",
                handle="stranger-coaching",
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
                failure_mode="cookie_expiry_not_enforced",
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
                started_at=_EVENT_BASE,
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
                "dimensions": {
                    "safety": {"score": 6, "max": 10, "signals": []},
                    "verification": {"score": 8, "max": 10, "signals": []},
                },
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

        # Seed three supervision events (one out of scope, two in scope).
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
                payload={
                    "prompt": "Please fix the session validation bug.",
                    "chars": 40,
                },
                occurred_at=_EVENT_BASE + timedelta(seconds=89),
            )
        )
        db.add(
            SupervisionEvent(
                session_id=_SESSION_ID,
                event_type="agent.responded",
                payload={
                    "response": "Here's the patch.",
                    "chars": 17,
                },
                occurred_at=_EVENT_BASE + timedelta(seconds=120),
            )
        )

        if note_body is not None:
            db.add(
                SessionNote(
                    session_id=_SESSION_ID,
                    body=note_body,
                    updated_at=_GRADED_AT,
                )
            )

        await db.commit()


def _make_cookie_for(user_id: uuid.UUID) -> str:
    from app.auth.session_cookie import _ALGORITHM
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


# ---------------------------------------------------------------------------
# Fake AnthropicClient: deterministic + count-tracking
# ---------------------------------------------------------------------------


def _content_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=42, output_tokens=21),
    )


class _StubClient:
    """Stub that returns a fixed coaching paragraph with anchor markers."""

    DEFAULT_OUTPUT = (
        "At 00:01:29 you sent the agent [event:2] a prompt about the "
        "session validation bug, but your notes flagged "
        '[note:"cookie expiration handling"] — a different hypothesis '
        "the session never tested."
    )

    def __init__(self, output: str | None = None) -> None:
        self.calls = 0
        self.output = output if output is not None else self.DEFAULT_OUTPUT

    async def messages_create(self, **_: Any) -> SimpleNamespace:
        self.calls += 1
        return _content_response(self.output)


class _FailingClient:
    """Stub that always raises — exercises the LLM-unavailable path."""

    def __init__(self) -> None:
        self.calls = 0

    async def messages_create(self, **_: Any) -> SimpleNamespace:
        self.calls += 1
        raise RuntimeError("simulated LLM outage")


def _install_client(monkeypatch, client: Any) -> None:
    monkeypatch.setattr(coaching_module, "_build_client", lambda: client)


# ---------------------------------------------------------------------------
# Pure-function tests (do not touch the FastAPI app)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coaching_skipped_for_empty_notes(db_engine, monkeypatch) -> None:
    """No notes (or whitespace-only) → returns None without invoking the LLM."""
    from app.config import get_settings
    from app.db import session as session_module

    await _seed(db_engine, note_body="   \n\t  ")
    client = _StubClient()
    _install_client(monkeypatch, client)

    async with session_module.AsyncSessionLocal() as db:
        result = await generate_coaching_reflection(
            db, submission_id=_SUBMISSION_ID, settings=get_settings()
        )
    assert result.outcome == CoachingOutcome.NO_NOTES
    assert result.payload is None
    assert client.calls == 0


@pytest.mark.asyncio
async def test_coaching_skipped_for_opted_out(db_engine, monkeypatch) -> None:
    """Opt-out short-circuits BEFORE any LLM call OR hashing of the notes."""
    from app.config import get_settings
    from app.db import session as session_module

    await _seed(db_engine, coaching_enabled=False)
    client = _StubClient()
    _install_client(monkeypatch, client)

    async with session_module.AsyncSessionLocal() as db:
        result = await generate_coaching_reflection(
            db, submission_id=_SUBMISSION_ID, settings=get_settings()
        )
    assert result.outcome == CoachingOutcome.OPTED_OUT
    assert result.payload is None
    assert client.calls == 0


def test_coaching_cache_key_includes_notes() -> None:
    """The cache content_hash is sensitive to the notes body.

    Same notes → same hash. One-character edit → different hash. The
    test exercises ``canonical_content_hash`` directly against the
    exact input dict shape ``generate_coaching_reflection`` uses, so a
    drift in either side surfaces immediately.
    """
    base_inputs = {
        "events_sha256": "e" * 64,
        "failure_mode": "cookie_expiry_not_enforced",
        "mission_id": "mission-a",
        "mission_version": 1,
        "rubric_version": "v1",
        "score_dimensions_sha256": "d" * 64,
    }

    h1 = hashlib.sha256(b"the bug is in cookie expiry").hexdigest()
    h2 = hashlib.sha256(b"the bug is in cookie expiry.").hexdigest()  # +1 char
    assert h1 != h2

    payload_a = {**base_inputs, "notes_sha256": h1}
    payload_b = {**base_inputs, "notes_sha256": h2}
    payload_a_dup = {**base_inputs, "notes_sha256": h1}

    key_a = canonical_content_hash(payload_a)
    key_b = canonical_content_hash(payload_b)
    key_a_dup = canonical_content_hash(payload_a_dup)

    assert key_a == key_a_dup, "identical inputs must produce identical hashes"
    assert key_a != key_b, "one-character note edit must bust the cache"


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coaching_endpoint_owner_only(
    client_with_db, db_engine, monkeypatch
) -> None:
    """Anonymous 401; share-token 403; stranger 403; owner 200."""
    from app.config import get_settings
    from app.reports.router import issue_share_token

    await _seed(db_engine)
    _install_client(monkeypatch, _StubClient())

    url = f"/api/v1/submissions/{_SUBMISSION_ID}/coaching"

    # Anonymous → 401
    client_with_db.cookies.clear()
    anon = await client_with_db.get(url)
    assert anon.status_code == 401, anon.text

    # Share-token → 403 (we explicitly refuse the share path).
    token, _ = issue_share_token(_SUBMISSION_ID, get_settings())
    share = await client_with_db.get(url, params={"share": token})
    assert share.status_code == 403, share.text

    # Non-owner (cookie present, different user) → 403
    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_cookie_for(_STRANGER_ID)
    )
    non_owner = await client_with_db.get(url)
    assert non_owner.status_code == 403, non_owner.text

    # Owner → 200
    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_cookie_for(_OWNER_ID)
    )
    owner = await client_with_db.get(url)
    assert owner.status_code == 200, owner.text
    body = owner.json()
    assert isinstance(body["reflection"], str) and body["reflection"]
    assert body["anchored_event_id"] is not None
    assert body["anchored_note_quote"] is not None


@pytest.mark.asyncio
async def test_coaching_endpoint_returns_cached_on_second_call(
    client_with_db, db_engine, monkeypatch
) -> None:
    """First GET invokes the stub; second GET reads the cached row."""
    from app.config import get_settings

    await _seed(db_engine)
    stub = _StubClient()
    _install_client(monkeypatch, stub)

    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_cookie_for(_OWNER_ID)
    )
    url = f"/api/v1/submissions/{_SUBMISSION_ID}/coaching"

    first = await client_with_db.get(url)
    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert stub.calls == 1

    second = await client_with_db.get(url)
    assert second.status_code == 200
    assert second.json()["cached"] is True, second.text
    # The stub MUST NOT be called again — the row came from llm_cache.
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_coaching_endpoint_503_on_llm_failure(
    client_with_db, db_engine, monkeypatch
) -> None:
    """Generator raises + no cache row → 503 ``{code: llm_unavailable}``."""
    from app.config import get_settings

    await _seed(db_engine)
    failing = _FailingClient()
    _install_client(monkeypatch, failing)

    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_cookie_for(_OWNER_ID)
    )
    resp = await client_with_db.get(
        f"/api/v1/submissions/{_SUBMISSION_ID}/coaching"
    )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    # Route now raises HTTPException(detail={...}) so the envelope is
    # the standard FastAPI shape ``{"detail": {"code": ..., "message": ...}}``
    # — the FE reads ``response.detail.code`` to render the section
    # placeholder.
    detail = body.get("detail") or {}
    assert detail.get("code") == "llm_unavailable"
    assert detail.get("message") == "coaching unavailable"
    assert failing.calls == 1


@pytest.mark.asyncio
async def test_coaching_endpoint_404_on_non_graded(
    client_with_db, db_engine, monkeypatch
) -> None:
    """A non-graded session 404s rather than falling through to coaching."""
    from app.config import get_settings
    from app.db import session as session_module

    await _seed(db_engine)
    _install_client(monkeypatch, _StubClient())

    # Demote the session — coaching must refuse to surface a half-built
    # report's reflection.
    async with session_module.AsyncSessionLocal() as db:
        row = await db.get(SessionRow, _SESSION_ID)
        row.status = "active"
        await db.commit()

    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_cookie_for(_OWNER_ID)
    )
    resp = await client_with_db.get(
        f"/api/v1/submissions/{_SUBMISSION_ID}/coaching"
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Account-deletion cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coaching_redacted_on_account_delete(
    db_engine, monkeypatch
) -> None:
    """Hard-delete drops the user's scratchpad_coaching cache rows."""
    from app.config import get_settings
    from app.db import session as session_module

    await _seed(db_engine)
    _install_client(monkeypatch, _StubClient())

    # Trigger a coaching generation so the cache + index rows exist.
    async with session_module.AsyncSessionLocal() as db:
        result = await generate_coaching_reflection(
            db, submission_id=_SUBMISSION_ID, settings=get_settings()
        )
        assert result.outcome == CoachingOutcome.OK
        assert result.payload is not None
        await db.commit()

    # Sanity: rows landed on both tables.
    async with session_module.AsyncSessionLocal() as db:
        cache_rows = list(
            (
                await db.execute(
                    select(LLMCache).where(LLMCache.domain == "scratchpad_coaching")
                )
            ).scalars()
        )
        index_rows = list(
            (
                await db.execute(
                    select(CoachingCacheUserIndex).where(
                        CoachingCacheUserIndex.user_id == _OWNER_ID
                    )
                )
            ).scalars()
        )
        assert len(cache_rows) == 1
        assert len(index_rows) == 1

    # Run the hard-delete worker for the owner.
    from app.workers.account_deletion import _hard_delete_user

    async with session_module.AsyncSessionLocal() as db:
        owner = await db.get(User, _OWNER_ID)
        # The worker expects the user to be past their grace; we don't
        # need to actually wait — the function takes the user object
        # directly.
        owner.deletion_scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.flush()
        await _hard_delete_user(db, owner)

    # Post-delete: both rows are gone.
    async with session_module.AsyncSessionLocal() as db:
        remaining_cache = list(
            (
                await db.execute(
                    select(LLMCache).where(LLMCache.domain == "scratchpad_coaching")
                )
            ).scalars()
        )
        remaining_index = list(
            (
                await db.execute(
                    select(CoachingCacheUserIndex).where(
                        CoachingCacheUserIndex.user_id == _OWNER_ID
                    )
                )
            ).scalars()
        )
    assert remaining_cache == [], (
        "coaching cache rows must be wiped on account hard-delete"
    )
    assert remaining_index == [], (
        "coaching_cache_user_index rows must be wiped on account hard-delete"
    )


@pytest.mark.asyncio
async def test_account_delete_preserves_shared_coaching_cache_row(
    db_engine, monkeypatch
) -> None:
    """A cache row referenced by a second user's index row survives hard-delete.

    Seeds the normal owner, runs a coaching generation so the cache
    row + owner's index row exist, manually adds a second user's
    index row pointing at the same cache row (simulating two users
    with coincidentally identical inputs), then hard-deletes the
    owner. The cache row + the second user's index row must remain;
    only the owner's index row is wiped. The shared-row counter
    surfaces the preservation.
    """
    from sqlalchemy import func as _func

    from app.config import get_settings
    from app.db import session as session_module
    from app.observability import llm_cache_shared_row_retained_total
    from app.workers.account_deletion import _hard_delete_user

    await _seed(db_engine)
    _install_client(monkeypatch, _StubClient())

    # 1. Generate so the cache + owner's index row land.
    async with session_module.AsyncSessionLocal() as db:
        result = await generate_coaching_reflection(
            db, submission_id=_SUBMISSION_ID, settings=get_settings()
        )
        assert result.outcome == CoachingOutcome.OK
        await db.commit()

    # 2. Stamp a second user's index row at the same cache id.
    async with session_module.AsyncSessionLocal() as db:
        cache_row = (
            await db.execute(
                select(LLMCache).where(LLMCache.domain == "scratchpad_coaching")
            )
        ).scalar_one()
        db.add(
            CoachingCacheUserIndex(
                user_id=_STRANGER_ID, llm_cache_id=cache_row.id
            )
        )
        await db.commit()
        shared_cache_id = cache_row.id

    # 3. Capture the shared-row counter so the assertion is delta-based
    # (other tests may have bumped it; the absolute count is not stable).
    before = llm_cache_shared_row_retained_total._value.get()  # type: ignore[attr-defined]

    # 4. Hard-delete the owner.
    async with session_module.AsyncSessionLocal() as db:
        owner = await db.get(User, _OWNER_ID)
        owner.deletion_scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.flush()
        await _hard_delete_user(db, owner)

    # 5. The cache row + the stranger's index row remain; the owner's
    # index row is gone.
    async with session_module.AsyncSessionLocal() as db:
        surviving_cache = (
            await db.execute(
                select(LLMCache).where(LLMCache.id == shared_cache_id)
            )
        ).scalar_one_or_none()
        assert surviving_cache is not None, (
            "shared coaching cache row must survive owner's hard-delete"
        )

        owner_index_count = (
            await db.execute(
                select(_func.count())
                .select_from(CoachingCacheUserIndex)
                .where(CoachingCacheUserIndex.user_id == _OWNER_ID)
            )
        ).scalar_one()
        assert owner_index_count == 0

        stranger_index_count = (
            await db.execute(
                select(_func.count())
                .select_from(CoachingCacheUserIndex)
                .where(CoachingCacheUserIndex.user_id == _STRANGER_ID)
            )
        ).scalar_one()
        assert stranger_index_count == 1, (
            "stranger's index row must survive owner's hard-delete"
        )

    # 6. Observability counter incremented for the preserved row.
    after = llm_cache_shared_row_retained_total._value.get()  # type: ignore[attr-defined]
    assert after - before >= 1, (
        f"shared-row counter must increment for the preserved cache row "
        f"(before={before}, after={after})"
    )


@pytest.mark.asyncio
async def test_coaching_consent_opt_out_invalidates_user_cache(
    client_with_db, db_engine, monkeypatch
) -> None:
    """POST /me/coaching-consent enabled=false deletes the user's cache rows.

    A second user's index row pointing at the same cache row keeps
    that cache row alive — opt-out only wipes the calling user's
    private slice of the index/cache tables.
    """
    from sqlalchemy import func as _func

    from app.config import get_settings
    from app.db import session as session_module

    await _seed(db_engine)
    _install_client(monkeypatch, _StubClient())

    # 1. Trigger a coaching generation as the owner.
    async with session_module.AsyncSessionLocal() as db:
        result = await generate_coaching_reflection(
            db, submission_id=_SUBMISSION_ID, settings=get_settings()
        )
        assert result.outcome == CoachingOutcome.OK
        await db.commit()

    # 2. Add a stranger's index row to the same cache id.
    async with session_module.AsyncSessionLocal() as db:
        cache_row = (
            await db.execute(
                select(LLMCache).where(LLMCache.domain == "scratchpad_coaching")
            )
        ).scalar_one()
        db.add(
            CoachingCacheUserIndex(
                user_id=_STRANGER_ID, llm_cache_id=cache_row.id
            )
        )
        await db.commit()
        shared_cache_id = cache_row.id

    # 3. Flip the owner's coaching consent to False via the route.
    client_with_db.cookies.set(
        get_settings().session_cookie_name, _make_cookie_for(_OWNER_ID)
    )
    resp = await client_with_db.post(
        "/api/v1/auth/me/coaching-consent",
        json={"coaching_reflections_enabled": False},
        headers={"X-CSRF-Token": "csrf"},
        cookies={"arena_csrf": "csrf"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["coaching_reflections_enabled"] is False

    # 4. The owner's index row is gone; the cache row + the stranger's
    # index row both survive because the cache row is shared.
    async with session_module.AsyncSessionLocal() as db:
        owner_index_count = (
            await db.execute(
                select(_func.count())
                .select_from(CoachingCacheUserIndex)
                .where(CoachingCacheUserIndex.user_id == _OWNER_ID)
            )
        ).scalar_one()
        assert owner_index_count == 0

        surviving_cache = (
            await db.execute(
                select(LLMCache).where(LLMCache.id == shared_cache_id)
            )
        ).scalar_one_or_none()
        assert surviving_cache is not None, (
            "shared cache row must survive opt-out because another user's "
            "index row still references it"
        )

        stranger_index_count = (
            await db.execute(
                select(_func.count())
                .select_from(CoachingCacheUserIndex)
                .where(CoachingCacheUserIndex.user_id == _STRANGER_ID)
            )
        ).scalar_one()
        assert stranger_index_count == 1
