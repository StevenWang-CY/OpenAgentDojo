"""``POST /sessions/{id}/reset`` endpoint contract (P0-12)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.file_change import FileChange
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.sandbox.types import SandboxHandle

_INITIAL_COMMIT = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
_CSRF = "test-csrf-reset-fixed"


class _RunResult:
    def __init__(self, exit_code: int, stdout: str = "", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = 5


class _FakeDriver:
    name = "local"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        # Map of cmd[0] → response; default success with empty stdout.
        self.responses: dict[tuple[str, ...], _RunResult] = {}

    async def run(self, handle, *, cmd, timeout_s, cwd):
        self.calls.append(list(cmd))
        key = tuple(cmd[:3])
        if key in self.responses:
            return self.responses[key]
        # Default: success.
        return _RunResult(0)


class _FakePool:
    def __init__(self, handle: SandboxHandle, driver: _FakeDriver) -> None:
        self.driver = driver
        self._handles = [handle]

    def handle_for(self, session_id):
        for h in self._handles:
            if h.session_id == session_id:
                return h
        return None

    def handles_snapshot(self):
        return list(self._handles)


async def _seed(db_engine, *, status: str = "active") -> tuple[uuid.UUID, uuid.UUID, _FakeDriver]:
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    owner_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=owner_id, email="o@a.local", display_name="O", handle="o"))
        db.add(
            Mission(
                id="m",
                title="t",
                difficulty="beginner",
                category="cat",
                repo_pack="p",
                initial_commit=_INITIAL_COMMIT,
                estimated_minutes=10,
                failure_mode="f",
                skills_tested=["s"],
                manifest_sha256="sha",
                version=1,
                published=True,
                expected_weak_dim="safety",
            )
        )
        db.add(
            SessionRow(
                id=session_id,
                user_id=owner_id,
                mission_id="m",
                status=status,
                attempt_index=1,
                started_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        await db.commit()

    return owner_id, session_id, _FakeDriver()


def _cookie(user_id: uuid.UUID) -> str:
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


def _attach_pool(app, driver: _FakeDriver, handle: SandboxHandle) -> None:
    app.state.sandbox_pool = _FakePool(handle, driver)


@pytest.mark.asyncio
async def test_reset_happy_path_emits_event_and_filechange(client, db_engine) -> None:
    owner_id, session_id, driver = await _seed(db_engine)

    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="m",
        session_id=session_id,
    )
    # ``git status --porcelain`` returns three modified entries.
    driver.responses[("git", "status", "--porcelain")] = _RunResult(
        0,
        stdout=" M src/auth.ts\n?? new.ts\n M lib/util.ts\n",
    )

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    app = client._transport.app  # type: ignore[attr-defined]
    _attach_pool(app, driver, handle)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["files_reset"] == 3
    assert body["new_head_commit"] == _INITIAL_COMMIT
    assert body["reset_count"] == 1

    # The driver received three commands in the right order.
    assert driver.calls[0][:3] == ["git", "status", "--porcelain"]
    assert driver.calls[1][:3] == ["git", "reset", "--hard"]
    assert driver.calls[1][3] == _INITIAL_COMMIT
    assert driver.calls[2][:3] == ["git", "clean", "-fd"]

    # A session.reset event landed with the expected payload shape.
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(SupervisionEvent).where(
                        SupervisionEvent.session_id == session_id,
                        SupervisionEvent.event_type == "session.reset",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["files_discarded"] == 3
        assert payload["had_agent_patch"] is False
        assert payload["seconds_into_session"] >= 0

        # A FileChange row with source='revert' was inserted.
        fc = (
            (await db.execute(select(FileChange).where(FileChange.session_id == session_id)))
            .scalars()
            .all()
        )
        assert len(fc) == 1
        assert fc[0].source == "revert"
        assert fc[0].path == "*"


@pytest.mark.asyncio
async def test_reset_requires_active_session(client, db_engine) -> None:
    owner_id, session_id, driver = await _seed(db_engine, status="graded")
    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="m",
        session_id=session_id,
    )
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    app = client._transport.app  # type: ignore[attr-defined]
    _attach_pool(app, driver, handle)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "session_not_active"


@pytest.mark.asyncio
async def test_reset_returns_500_on_git_reset_failure(client, db_engine) -> None:
    owner_id, session_id, driver = await _seed(db_engine)
    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="m",
        session_id=session_id,
    )
    # status succeeds, but reset --hard fails (e.g. unreachable commit).
    driver.responses[("git", "status", "--porcelain")] = _RunResult(0, stdout=" M x\n")
    driver.responses[("git", "reset", "--hard")] = _RunResult(1, stderr="fatal: bad object")

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    app = client._transport.app  # type: ignore[attr-defined]
    _attach_pool(app, driver, handle)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "git_reset_failed"


@pytest.mark.asyncio
async def test_reset_count_increments_across_calls(client, db_engine) -> None:
    owner_id, session_id, driver = await _seed(db_engine)
    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="m",
        session_id=session_id,
    )
    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    app = client._transport.app  # type: ignore[attr-defined]
    _attach_pool(app, driver, handle)

    r1 = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["reset_count"] == 1
    r2 = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["reset_count"] == 2


@pytest.mark.asyncio
async def test_reset_payload_sets_had_agent_patch_when_present(client, db_engine) -> None:
    owner_id, session_id, driver = await _seed(db_engine)
    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="m",
        session_id=session_id,
    )
    # Seed a prior patch.applied event so had_agent_patch flips to True.
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        db.add(
            SupervisionEvent(
                session_id=session_id,
                event_type="patch.applied",
                payload={"path": "x"},
                occurred_at=datetime.now(UTC),
            )
        )
        await db.commit()

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    app = client._transport.app  # type: ignore[attr-defined]
    _attach_pool(app, driver, handle)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 200, resp.text

    async with session_module.AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(SupervisionEvent).where(
                    SupervisionEvent.session_id == session_id,
                    SupervisionEvent.event_type == "session.reset",
                )
            )
        ).scalar_one()
        assert row.payload["had_agent_patch"] is True


@pytest.mark.asyncio
async def test_reset_returns_500_on_git_clean_failure(client, db_engine) -> None:
    """``git clean -fd`` returning non-zero must surface as a 500.

    Regression — previously the driver's ``RunResult`` was captured but
    the exit code was never checked, so a permission-denied (e.g. a
    write-protected ``node_modules`` tree) silently left orphan untracked
    files behind while the endpoint reported success.
    """
    owner_id, session_id, driver = await _seed(db_engine)
    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="m",
        session_id=session_id,
    )
    # status + reset succeed; clean fails (permission denied on a
    # write-protected node_modules tree is the canonical real-world
    # cause).
    driver.responses[("git", "status", "--porcelain")] = _RunResult(0, stdout=" M x\n")
    driver.responses[("git", "clean", "-fd")] = _RunResult(
        128, stderr="warning: failed to remove node_modules/.cache: Permission denied"
    )

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    app = client._transport.app  # type: ignore[attr-defined]
    _attach_pool(app, driver, handle)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["code"] == "git_clean_failed"


@pytest.mark.asyncio
async def test_reset_is_rate_limited(client, db_engine) -> None:
    """``POST /reset`` is capped at 10/min/user (P0-DoS).

    Without a per-route rule a single authenticated client could pin a
    worker by spamming resets — every call shells out to ``git reset
    --hard`` + ``git clean -fd`` inside the sandbox.
    """
    owner_id, session_id, driver = await _seed(db_engine)
    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="m",
        session_id=session_id,
    )

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)

    app = client._transport.app  # type: ignore[attr-defined]
    _attach_pool(app, driver, handle)

    # Fire 10 calls — all should succeed (limit == 10/min).
    for _ in range(10):
        ok = await client.post(
            f"/api/v1/sessions/{session_id}/reset",
            headers={"X-CSRF-Token": _CSRF},
        )
        assert ok.status_code == 200, ok.text

    # The 11th in the same minute must be throttled.
    throttled = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert throttled.status_code == 429, throttled.text
    assert throttled.json()["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_reset_returns_403_for_stranger(client, db_engine) -> None:
    _owner, session_id, driver = await _seed(db_engine)
    handle = SandboxHandle(
        id="h1",
        driver="local",
        workdir=Path("/tmp/arena-fake"),
        mission_id="m",
        session_id=session_id,
    )

    other_id = uuid.uuid4()
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=other_id, email="other@a.local", display_name="Other", handle="other"))
        await db.commit()

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(other_id))
    client.cookies.set("arena_csrf", _CSRF)

    app = client._transport.app  # type: ignore[attr-defined]
    _attach_pool(app, driver, handle)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/reset",
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 403, resp.text
