"""P0-5 — ``GET`` / ``POST`` ``/auth/me/consent`` end-to-end behaviour.

Covers the contract surface, the append-only invariant, CASCADE on user
deletion, prometheus counter increments, supervision-event emission on the
``account_events`` table (migration 0017 renamed ``consent_events`` →
``account_events`` once P0-6 needed to land ``account.*`` literals on the
same log), and the CSRF / auth gates that protect the POST.

The test suite intentionally exercises the full FastAPI middleware stack
(CSRF, request-scoped DB) rather than calling the handlers directly — the
acceptance criteria explicitly call for CSRF rejection paths and for the
prometheus counter to advance through the live request path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_auth
from app.config import get_settings
from app.db.session import get_db
from app.main import create_app
from app.models.user import User
from app.models.user_consent import AccountEvent, UserConsent

_CSRF = "test-csrf-token-consent-fixed"


@pytest_asyncio.fixture
async def consent_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"consent-{uuid.uuid4().hex[:8]}@example.com",
        handle=f"c{uuid.uuid4().hex[:8]}",
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _make_app(db_session: AsyncSession, user: User | None) -> Any:
    """Build a FastAPI app whose DB and auth deps are overridden for the test."""
    app = create_app()

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    if user is not None:
        def _as_user() -> User:
            return user

        app.dependency_overrides[require_auth] = _as_user
    return app


def _csrf_kwargs() -> dict[str, Any]:
    return {
        "headers": {"X-CSRF-Token": _CSRF},
        "cookies": {"arena_csrf": _CSRF},
    }


def _consent_counter_value(kind: str, granted: str) -> float:
    """Read ``consent_recorded_total{kind=..., granted=...}`` from the registry."""
    from app.observability import REGISTRY

    value = REGISTRY.get_sample_value(
        "consent_recorded_total",
        {"kind": kind, "granted": granted},
    )
    return float(value) if value is not None else 0.0


@pytest.mark.asyncio
async def test_get_returns_all_nulls_for_new_user(
    consent_user: User, db_session: AsyncSession
) -> None:
    """A brand-new user has no consent rows — every kind serialises to None."""
    app = _make_app(db_session, consent_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/api/v1/auth/me/consent")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"analytics": None, "functional": None, "marketing": None}


@pytest.mark.asyncio
async def test_post_then_get_reflects_record(
    consent_user: User, db_session: AsyncSession
) -> None:
    """POST analytics granted=true → GET shows the freshly-recorded row."""
    app = _make_app(db_session, consent_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        post_resp = await ac.post(
            "/api/v1/auth/me/consent",
            json={"kind": "analytics", "granted": True},
            **_csrf_kwargs(),
        )
        assert post_resp.status_code == 204, post_resp.text

        get_resp = await ac.get("/api/v1/auth/me/consent")
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    record = body["analytics"]
    assert record is not None
    assert record["granted"] is True
    assert record["version"] == get_settings().consent_policy_version
    # ``at`` must be a parseable ISO timestamp.
    parsed = datetime.fromisoformat(record["at"].replace("Z", "+00:00"))
    assert isinstance(parsed, datetime)
    # The other two kinds remain unrecorded.
    assert body["functional"] is None
    assert body["marketing"] is None


@pytest.mark.asyncio
async def test_post_same_kind_twice_get_returns_latest(
    consent_user: User, db_session: AsyncSession
) -> None:
    """Granted then revoked on the same kind → GET shows the revocation."""
    app = _make_app(db_session, consent_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r1 = await ac.post(
            "/api/v1/auth/me/consent",
            json={"kind": "analytics", "granted": True},
            **_csrf_kwargs(),
        )
        assert r1.status_code == 204, r1.text
        r2 = await ac.post(
            "/api/v1/auth/me/consent",
            json={"kind": "analytics", "granted": False},
            **_csrf_kwargs(),
        )
        assert r2.status_code == 204, r2.text

        get_resp = await ac.get("/api/v1/auth/me/consent")
    body = get_resp.json()
    assert body["analytics"] is not None
    assert body["analytics"]["granted"] is False


@pytest.mark.asyncio
async def test_posts_are_append_only(
    consent_user: User, db_session: AsyncSession
) -> None:
    """N POSTs against the same kind insert N rows (no UPDATE / no dedupe)."""
    app = _make_app(db_session, consent_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        for granted in (True, False, True):
            resp = await ac.post(
                "/api/v1/auth/me/consent",
                json={"kind": "functional", "granted": granted},
                **_csrf_kwargs(),
            )
            assert resp.status_code == 204, resp.text

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(UserConsent)
            .where(UserConsent.user_id == consent_user.id)
        )
    ).scalar_one()
    assert count == 3


@pytest.mark.asyncio
async def test_post_increments_prometheus_counter(
    consent_user: User, db_session: AsyncSession
) -> None:
    """Each successful POST advances ``consent_recorded_total`` by one."""
    before_granted = _consent_counter_value("marketing", "true")
    before_revoked = _consent_counter_value("marketing", "false")

    app = _make_app(db_session, consent_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r1 = await ac.post(
            "/api/v1/auth/me/consent",
            json={"kind": "marketing", "granted": True},
            **_csrf_kwargs(),
        )
        assert r1.status_code == 204, r1.text
        r2 = await ac.post(
            "/api/v1/auth/me/consent",
            json={"kind": "marketing", "granted": False},
            **_csrf_kwargs(),
        )
        assert r2.status_code == 204, r2.text

    after_granted = _consent_counter_value("marketing", "true")
    after_revoked = _consent_counter_value("marketing", "false")
    assert after_granted == pytest.approx(before_granted + 1)
    assert after_revoked == pytest.approx(before_revoked + 1)


@pytest.mark.asyncio
async def test_cascade_on_user_delete_drops_consents(
    consent_user: User, db_session: AsyncSession
) -> None:
    """Deleting the user cascades the consent rows (GDPR Article 17).

    SQLite does not enforce ON DELETE CASCADE unless ``PRAGMA foreign_keys``
    is ON; the production-grade Postgres path always enforces it. We enable
    the pragma for this connection so the test exercises the same semantic
    contract that the migration declares.
    """
    await db_session.execute(text("PRAGMA foreign_keys = ON"))

    app = _make_app(db_session, consent_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        for kind in ("analytics", "functional"):
            resp = await ac.post(
                "/api/v1/auth/me/consent",
                json={"kind": kind, "granted": True},
                **_csrf_kwargs(),
            )
            assert resp.status_code == 204, resp.text

    pre_count = (
        await db_session.execute(
            select(func.count())
            .select_from(UserConsent)
            .where(UserConsent.user_id == consent_user.id)
        )
    ).scalar_one()
    assert pre_count == 2

    # Hard-delete the user; the ON DELETE CASCADE on user_consents.user_id
    # must drop the dependent rows in a single statement.
    await db_session.delete(consent_user)
    await db_session.commit()

    post_count = (
        await db_session.execute(
            select(func.count())
            .select_from(UserConsent)
            .where(UserConsent.user_id == consent_user.id)
        )
    ).scalar_one()
    assert post_count == 0


@pytest.mark.asyncio
async def test_post_emits_supervision_event_with_payload(
    consent_user: User, db_session: AsyncSession
) -> None:
    """Each POST persists exactly one ``account_events`` row with the right shape."""
    app = _make_app(db_session, consent_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        granted_resp = await ac.post(
            "/api/v1/auth/me/consent",
            json={"kind": "analytics", "granted": True},
            **_csrf_kwargs(),
        )
        assert granted_resp.status_code == 204, granted_resp.text
        revoked_resp = await ac.post(
            "/api/v1/auth/me/consent",
            json={"kind": "analytics", "granted": False},
            **_csrf_kwargs(),
        )
        assert revoked_resp.status_code == 204, revoked_resp.text

    events = (
        await db_session.execute(
            select(AccountEvent)
            .where(AccountEvent.user_id == consent_user.id)
            .order_by(AccountEvent.id)
        )
    ).scalars().all()
    assert [e.event_type for e in events] == [
        "consent.granted",
        "consent.revoked",
    ]
    policy_version = get_settings().consent_policy_version
    for event in events:
        assert event.payload == {"kind": "analytics", "version": policy_version}


@pytest.mark.asyncio
async def test_post_without_csrf_returns_403(
    consent_user: User, db_session: AsyncSession
) -> None:
    """The CSRF middleware must reject a POST that omits the double-submit pair."""
    app = _make_app(db_session, consent_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/api/v1/auth/me/consent",
            json={"kind": "analytics", "granted": True},
        )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body.get("code") == "csrf_invalid"


@pytest.mark.asyncio
async def test_anonymous_requests_are_unauthorized(
    db_session: AsyncSession,
) -> None:
    """Both GET and POST require auth — no auth → 401.

    We deliberately disable the dev-auth IP fallback so the unauth path
    isn't masked by the synthetic dev user that ``get_current_user`` would
    otherwise mint in ``arena_env == 'development'``.
    """
    settings = get_settings()
    original_env = settings.arena_env
    original_dev_auth = settings.allow_dev_auth
    settings.arena_env = "test"  # disables dev-auth fallback
    settings.allow_dev_auth = False
    try:
        app = _make_app(db_session, user=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            get_resp = await ac.get("/api/v1/auth/me/consent")
            assert get_resp.status_code == 401, get_resp.text

            post_resp = await ac.post(
                "/api/v1/auth/me/consent",
                json={"kind": "analytics", "granted": True},
                **_csrf_kwargs(),
            )
            assert post_resp.status_code == 401, post_resp.text
    finally:
        settings.arena_env = original_env
        settings.allow_dev_auth = original_dev_auth
