"""Phase 4.A.T8 — workspace search surfaces real ripgrep exit codes.

The driver returns ``(matches, truncated, total, exit_code)``; the
router emits ``exit_code`` on the ``command.run`` event payload AND
fires a ``validator.flag{kind="search_error"}`` when the code is
non-zero non-empty (rc=1 legitimately means "no matches" so the router
doesn't flag on it).

Test: monkeypatch the sandbox driver's ``search`` to return
``([], False, 0, 2)``; POST the search; assert the persisted
``command.run`` event carries exit_code=2 AND a separate
``validator.flag{kind="search_error"}`` event landed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.session_cookie import issue_session_cookie
from app.config import get_settings
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User


class _CookieCapture:
    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def set_cookie(self, *, key: str, value: str, **_: object) -> None:
        self.cookies[key] = value


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


class _FakeDriver:
    """Stand-in pool driver whose ``search`` returns rc=2."""

    name = "local"

    async def search(self, handle, query, *, glob, case_sensitive, regex, max_results):
        return [], False, 0, 2


class _FakePool:
    def __init__(self) -> None:
        self.driver = _FakeDriver()
        self._handle = _FakeHandle()

    def handle_for(self, session_id):
        return self._handle

    def handles_snapshot(self):
        return [self._handle]


class _FakeHandle:
    id = "handle-xyz"

    def __init__(self) -> None:
        from pathlib import Path

        self.workdir = Path("/tmp/nonexistent")
        self.session_id = None


@pytest.mark.asyncio
async def test_search_exit_code_2_emits_validator_flag(client_with_db, db_engine) -> None:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    async with factory() as db:
        user = User(
            id=user_id,
            email=f"srch-{user_id.hex[:8]}@a.local",
            handle=f"sr-{user_id.hex[:6]}",
            session_epoch=1,
        )
        db.add(user)
        session = SessionRow(
            user_id=user_id,
            mission_id="m-x",
            status="active",
            mode="self_study",
            last_activity_at=datetime.now(UTC),
        )
        db.add(session)
        await db.commit()
        sid = session.id
        handle = _FakeHandle()
        handle.session_id = sid

    # Replace the app's sandbox_pool with our fake.
    pool = _FakePool()
    pool._handle.session_id = sid
    client_with_db._transport.app.state.sandbox_pool = pool  # type: ignore[attr-defined]

    settings = get_settings()
    cap = _CookieCapture()
    issue_session_cookie(cap, str(user_id), settings, epoch=1)
    client_with_db.cookies.set(
        settings.session_cookie_name, cap.cookies[settings.session_cookie_name]
    )
    client_with_db.cookies.set("arena_csrf", "tok")

    resp = await client_with_db.post(
        f"/api/v1/sessions/{sid}/files/search",
        json={
            "query": "needle",
            "glob": None,
            "case_sensitive": False,
            "regex": False,
            "max_results": 100,
        },
        headers={"X-Csrf-Token": "tok"},
    )
    assert resp.status_code == 200, resp.text

    # Inspect the persisted events. The command.run event MUST carry
    # exit_code=2 and there MUST be a validator.flag with
    # kind=search_error.
    async with factory() as db:
        events = list(
            (
                await db.execute(select(SupervisionEvent).where(SupervisionEvent.session_id == sid))
            ).scalars()
        )

    cmd_event = next((e for e in events if e.event_type == "command.run"), None)
    assert cmd_event is not None, "command.run event was not emitted"
    payload = cmd_event.payload or {}
    assert payload.get("exit_code") == 2, f"expected exit_code=2 on command.run, got {payload}"

    flag_event = next(
        (
            e
            for e in events
            if e.event_type == "validator.flag" and (e.payload or {}).get("kind") == "search_error"
        ),
        None,
    )
    assert flag_event is not None, (
        f"validator.flag with kind=search_error was not emitted; events={[e.event_type for e in events]}"
    )
