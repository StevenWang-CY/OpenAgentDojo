"""Phase 4.A.T2 — ``GET /sessions/{id}/submission`` surfaces ``verified``.

The submit/grading runner stamps ``submissions.verified`` from
``session.mode == 'proctored'`` at grade time. The owner-facing read
endpoint must echo that flag back on the wire so the FE can render the
verified-attempt chrome (badge, chip, locked-edits) without re-deriving
from the session row.

Tests:
  * proctored session → ``response.json()["verified"] == True``
  * self-study session → ``response.json()["verified"] == False``
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.session_cookie import issue_session_cookie
from app.config import get_settings
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User


class _CookieCapture:
    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def set_cookie(self, *, key: str, value: str, **_: object) -> None:
        self.cookies[key] = value


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _seed(session_factory, *, mode: str, verified: bool) -> tuple[uuid.UUID, str]:
    """Insert a graded session + submission and return (session_id, cookie)."""
    user_id = uuid.uuid4()
    async with session_factory() as db:
        user = User(
            id=user_id,
            email=f"verified-{user_id.hex[:8]}@a.local",
            handle=f"vrf-{user_id.hex[:6]}",
            session_epoch=1,
        )
        db.add(user)
        session = SessionRow(
            user_id=user_id,
            mission_id="mission-x",
            status="graded",
            mode=mode,
        )
        db.add(session)
        await db.flush()
        submission = Submission(
            session_id=session.id,
            final_diff="",
            visible_test_results=[],
            hidden_test_results=[],
            validator_results=[],
            score_report={"total": 80, "dimensions": {}, "missed_failure_mode": False},
            total_score=80,
            verified=verified,
            created_at=datetime.now(UTC),
        )
        db.add(submission)
        await db.commit()
        sid = session.id

    settings = get_settings()
    cap = _CookieCapture()
    issue_session_cookie(cap, str(user_id), settings, epoch=1)
    return sid, cap.cookies[settings.session_cookie_name]


@pytest.mark.asyncio
async def test_proctored_submission_returns_verified_true(client_with_db, db_engine) -> None:
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    sid, cookie = await _seed(session_factory, mode="proctored", verified=True)

    settings = get_settings()
    client_with_db.cookies.set(settings.session_cookie_name, cookie)

    resp = await client_with_db.get(f"/api/v1/sessions/{sid}/submission")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verified"] is True


@pytest.mark.asyncio
async def test_self_study_submission_returns_verified_false(client_with_db, db_engine) -> None:
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    sid, cookie = await _seed(session_factory, mode="self_study", verified=False)

    settings = get_settings()
    client_with_db.cookies.set(settings.session_cookie_name, cookie)

    resp = await client_with_db.get(f"/api/v1/sessions/{sid}/submission")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verified"] is False
