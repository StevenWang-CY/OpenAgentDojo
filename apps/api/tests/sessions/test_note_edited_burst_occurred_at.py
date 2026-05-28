"""P4.1B remediation — coalesce burst preserves burst-start occurred_at.

Before the fix, a coalesced ``note.edited`` event's ``occurred_at`` was
overwritten on every PUT inside the coalescing window. The post-mortem
timeline therefore rendered every edit burst at the TAIL of the burst
instead of at its head — operators trying to understand "when did the
user start thinking about this?" got the answer "the moment they stopped
typing", which inverts the signal.

This file proves the contract:

  * a burst of N PUTs spread over the coalescing window collapses to
    ONE event whose ``occurred_at`` is the timestamp of the FIRST PUT
    (the burst's head), NOT the last.
"""

from __future__ import annotations

import asyncio
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

_CSRF = "test-csrf-token-notes-burst"


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
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"bst{unique}@example.com",
        handle=f"bst{unique}",
        display_name="Owner",
    )
    mission = Mission(
        id=f"notes-burst-mission-{unique}",
        title="Notes Mission burst occurred_at",
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
async def test_burst_preserves_first_put_occurred_at(
    setup, db_session: AsyncSession
) -> None:
    """Five PUTs within the coalesce window collapse to ONE event whose
    occurred_at equals the FIRST PUT's timestamp (within a tight tolerance).

    We capture wall-clock just before the first PUT and after the last;
    the coalesced event's occurred_at must hug the *first* boundary
    rather than drift forward to the last.
    """
    user = setup["user"]
    session = setup["session"]
    app = _make_app(db_session, user)
    transport = ASGITransport(app=app)

    before_first = datetime.now(UTC)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Five PUTs separated by tiny sleeps so wall-clock can distinguish
        # the first from the last even on a fast CI runner. The sleeps
        # are deliberately small (50 ms each) to keep the test fast while
        # producing a non-trivial spread (~200 ms total) between first and
        # last PUT.
        for body in (
            "burst write 1",
            "burst write 2",
            "burst write 3",
            "burst write 4",
            "burst write 5",
        ):
            r = await ac.put(
                f"/api/v1/sessions/{session.id}/note",
                json={"body": body},
                **_csrf_kwargs(),
            )
            assert r.status_code == 200, r.text
            await asyncio.sleep(0.05)
    after_last = datetime.now(UTC)

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
    # All five PUTs collapsed into one row — that's the coalescing
    # contract this remediation depends on.
    assert len(events) == 1, [e.payload for e in events]
    occurred_at = events[0].occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)

    # occurred_at must hug the FIRST PUT, NOT the LAST PUT. We allow a
    # 250 ms forward tolerance from before_first (the route does some
    # work before it timestamps) and a 250 ms backward tolerance — but
    # it must NOT be at or near after_last (the pre-fix behaviour).
    drift_from_first = (occurred_at - before_first).total_seconds()
    drift_from_last = (after_last - occurred_at).total_seconds()
    assert drift_from_first >= -0.05, (
        f"occurred_at {occurred_at} predates before_first {before_first}"
    )
    assert drift_from_first <= 0.25, (
        f"occurred_at {occurred_at} drifted {drift_from_first:.3f}s past "
        f"before_first {before_first} — burst-start anchor violated"
    )
    # Sanity: occurred_at must be CLOSER to before_first than to
    # after_last. This is the load-bearing assertion — the pre-fix code
    # would put occurred_at right at after_last (within a few ms),
    # which is the opposite of the corrected semantics.
    assert drift_from_first < drift_from_last, (
        f"occurred_at {occurred_at} drifted toward LAST PUT "
        f"(after_last={after_last}, before_first={before_first}); "
        "burst-start anchor regressed."
    )
