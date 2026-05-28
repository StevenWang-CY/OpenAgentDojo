"""P4.1B remediation — ``note.edited.seconds_since_last_edit`` semantics.

Before the fix, ``seconds_since_last_edit`` was computed against the
``SessionNote`` row's ``updated_at``. Inside a coalesce burst the row
is upserted on every PUT, so the delta was always ~0 — the FE timeline
would always show "0s since last edit" even when the user paused for
half a minute between bursts. The corrected semantics: time since the
previous ``note.edited`` *event*, NOT the row's updated_at.

This file proves the contract:

  * first edit reports ``seconds_since_last_edit == 0`` (documented);
  * second edit ~10s later reports ~10s, regardless of the row's
    updated_at (which has already been bumped);
  * a coalesce burst reports time-since-the-*burst's-prior-event*, not
    since the latest write that triggered the coalesce;
  * after a 35s gap a fresh event fires whose seconds_since reflects
    the gap.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_auth
from app.db.session import get_db
from app.main import create_app
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User

_CSRF = "test-csrf-token-notes-seconds-since"


def _csrf_kwargs() -> dict:
    return {
        "headers": {"X-CSRF-Token": _CSRF},
        "cookies": {"arena_csrf": _CSRF},
    }


def _make_app(db_session: AsyncSession, user: User):
    app = create_app()

    async def _override_db():
        yield db_session

    def _as_user() -> User:
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _as_user
    return app


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession) -> dict:
    """Seed a user + mission + active session."""
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"sec{unique}@example.com",
        handle=f"sec{unique}",
        display_name="Owner",
    )
    mission = Mission(
        id=f"notes-secs-mission-{unique}",
        title="Notes Mission seconds_since",
        difficulty="beginner",
        category="testing",
        repo_pack="fullstack-auth-demo",
        initial_commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        estimated_minutes=10,
        failure_mode="test_failure_mode",
        skills_tested=["s"],
        manifest_sha256="0" * 64,
        version=1,
        published=True,
        expected_weak_dim="safety",
    )
    db_session.add_all([user, mission])
    await db_session.flush()
    session = SessionRow(
        user_id=user.id,
        mission_id=mission.id,
        status="active",
        started_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add(session)
    await db_session.commit()
    return {"user": user, "session": session}


@pytest.mark.asyncio
async def test_first_edit_reports_zero(setup, db_session: AsyncSession) -> None:
    """The very first ``note.edited`` event reports ``seconds_since=0``."""
    user = setup["user"]
    session = setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "first note"},
            **_csrf_kwargs(),
        )
    assert resp.status_code == 200, resp.text

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].payload["seconds_since_last_edit"] == 0


@pytest.mark.asyncio
async def test_second_edit_after_gap_reports_seconds_against_event(
    setup, db_session: AsyncSession
) -> None:
    """A second edit after a 35s gap reports seconds_since against the
    *event*'s occurred_at, not against the row's updated_at.

    We back-date the previous event AND the SessionNote row to 35s ago.
    The fix means the seconds_since computation reads the event time
    (35s) and reports ~35; the pre-fix code would read the row time
    (35s as well in this case — but if the row had been later-updated
    by an intervening coalesce, the reported delta would be much
    smaller). The test is sharpened in the burst-test below where the
    row is upserted but the event time stays anchored.
    """
    user = setup["user"]
    session = setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        first = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "first"},
            **_csrf_kwargs(),
        )
        assert first.status_code == 200, first.text

        # Back-date the existing event past the 30s coalescing window so
        # the second PUT emits a fresh event.
        far_past = datetime.now(UTC) - timedelta(seconds=35)
        ev = (
            await db_session.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
            )
        ).scalar_one()
        ev.occurred_at = far_past
        # Leave the SessionNote.updated_at UNCHANGED — under the new
        # semantics it doesn't matter, but if we accidentally read from
        # updated_at the assertion below will fail.
        await db_session.commit()

        second = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "second body"},
            **_csrf_kwargs(),
        )
        assert second.status_code == 200, second.text

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent)
                .where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
                .order_by(SupervisionEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 2
    latest = events[-1]
    seconds = latest.payload["seconds_since_last_edit"]
    # The gap we constructed was 35s; the test waits ~no time so the
    # measured delta should be ~35s. Tolerate +/- 5s for scheduler
    # jitter on slow CI runners.
    assert 30 <= seconds <= 40, latest.payload


@pytest.mark.asyncio
async def test_coalesce_burst_uses_previous_event_not_row(
    setup, db_session: AsyncSession
) -> None:
    """A coalesce burst reports seconds_since against the burst's prior
    event, not against the latest write that triggered the coalesce.

    Setup: one ``note.edited`` event already exists 25s ago for an
    earlier burst. Then within the coalescing window we land three PUTs
    that coalesce into a single second event. The coalesced event's
    ``seconds_since_last_edit`` must reflect time since the FIRST
    event — NOT since the row's updated_at (which is "just now" because
    each PUT upserts the row).
    """
    user = setup["user"]
    session = setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # First PUT — emits the prior burst's event.
        first = await ac.put(
            f"/api/v1/sessions/{session.id}/note",
            json={"body": "prior burst body"},
            **_csrf_kwargs(),
        )
        assert first.status_code == 200, first.text

        # Back-date the prior event so the next PUT falls OUTSIDE the
        # coalesce window and starts a new burst.
        prior_event_at = datetime.now(UTC) - timedelta(seconds=35)
        ev = (
            await db_session.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
            )
        ).scalar_one()
        ev.occurred_at = prior_event_at
        await db_session.commit()

        # Second burst: three PUTs in quick succession, all inside the
        # 30s coalesce window relative to the second event.
        for body in ("burst-1", "burst-2", "burst-3"):
            r = await ac.put(
                f"/api/v1/sessions/{session.id}/note",
                json={"body": body},
                **_csrf_kwargs(),
            )
            assert r.status_code == 200, r.text

    events = (
        (
            await db_session.execute(
                select(SupervisionEvent)
                .where(
                    SupervisionEvent.session_id == session.id,
                    SupervisionEvent.event_type == "note.edited",
                )
                .order_by(SupervisionEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    # Two events total: the original (back-dated) + the coalesced burst.
    assert len(events) == 2, [e.payload for e in events]
    coalesced = events[-1]
    # The coalesced event's seconds_since must reflect the gap against
    # the *previous* event (~35s), not against the row's updated_at
    # (which is "now-ish" because every PUT upsert bumped it).
    seconds = coalesced.payload["seconds_since_last_edit"]
    assert 30 <= seconds <= 45, coalesced.payload
