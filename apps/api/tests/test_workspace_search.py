"""P0-9 — Find-in-files / repo-wide workspace search tests.

These tests cover three surfaces:

  * The local driver's ``list_files`` + ``search`` against a real git workspace
    (validates the ripgrep subprocess wiring + the JSON parser).
  * The ``GET /sessions/{id}/files/list`` endpoint contract (query filter,
    sorting, truncation, ownership gates).
  * The ``POST /sessions/{id}/files/search`` endpoint contract (success,
    empty/oversized query, invalid regex, timeout, ``command.run`` event).

The endpoint tests use a fake driver so they don't depend on ripgrep being
installed on every CI runner; the driver-level test skips when ripgrep is
missing.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.models.command_run import CommandRun
from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.sandbox.driver import InvalidRegexError, SearchTimeoutError
from app.sandbox.local_driver import LocalSandboxDriver
from app.sandbox.types import SandboxHandle

_CSRF = "test-csrf-search-1234"
_INITIAL_COMMIT = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

RG_PRESENT = shutil.which("rg") is not None
GIT_PRESENT = shutil.which("git") is not None


# ---------------------------------------------------------------------------
# Driver-level smoke tests (real ripgrep, real git)
# ---------------------------------------------------------------------------


class _FakeMission:
    id = "fake-mission"

    class repo:  # noqa: N801
        pack = "__no_such_pack__"
        language_runtime = "node20"


@pytest.mark.skipif(
    not (RG_PRESENT and GIT_PRESENT),
    reason="ripgrep + git are required for the driver-level search test",
)
@pytest.mark.asyncio
async def test_local_driver_search_returns_match(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_WORKDIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()

    driver = LocalSandboxDriver()
    mission = _FakeMission()
    session_id = uuid.uuid4()
    handle = await driver.provision(mission, session_id)
    try:
        # Seed a tiny repo with a unique token.
        await driver.write_file(handle, "src/auth.ts", b"export const SECRET = 'abc';\n")
        await driver.write_file(handle, "src/index.ts", b"console.log('hi');\n")
        await driver.write_file(handle, "README.md", b"# Project\n\nUseful SECRET docs\n")
        await driver.run(handle, ["git", "add", "-A"])
        await driver.run(handle, ["git", "commit", "-q", "-m", "seed"])

        # list_files honours git-tracked + sorting.
        paths = await driver.list_files(handle, max_files=100)
        assert "README.md" in paths
        assert "src/auth.ts" in paths
        # Shallower paths first.
        assert paths.index("README.md") < paths.index("src/auth.ts")

        matches, truncated, total = await driver.search(
            handle,
            "SECRET",
            glob=None,
            case_sensitive=True,
            regex=False,
            max_results=50,
        )
        assert truncated is False
        assert total == len(matches)
        assert total >= 2
        files_hit = {m["path"] for m in matches}
        assert "src/auth.ts" in files_hit
        assert "README.md" in files_hit
        for match in matches:
            assert match["line_number"] >= 1
            assert "SECRET" in match["line_text"]
            assert 0 <= match["match_start"] <= match["match_end"] <= len(match["line_text"])

        # Glob filter narrows results to one file.
        scoped, _, _ = await driver.search(
            handle,
            "SECRET",
            glob="src/**",
            case_sensitive=True,
            regex=False,
            max_results=50,
        )
        assert {m["path"] for m in scoped} == {"src/auth.ts"}
    finally:
        await driver.destroy(handle)


@pytest.mark.skipif(
    not (RG_PRESENT and GIT_PRESENT),
    reason="ripgrep + git are required for invalid-regex coverage",
)
@pytest.mark.asyncio
async def test_local_driver_search_invalid_regex_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_WORKDIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()

    driver = LocalSandboxDriver()
    handle = await driver.provision(_FakeMission(), uuid.uuid4())
    try:
        with pytest.raises(InvalidRegexError):
            await driver.search(
                handle,
                "(unclosed",
                glob=None,
                case_sensitive=False,
                regex=True,
                max_results=50,
            )
    finally:
        await driver.destroy(handle)


# ---------------------------------------------------------------------------
# Endpoint tests (fake driver)
# ---------------------------------------------------------------------------


class _FakeDriverState:
    """Per-test mutable record of driver responses."""

    def __init__(self) -> None:
        self.list_response: list[str] = []
        # Phase 4.A.19 — drivers now return ``(matches, truncated, total,
        # exit_code)`` so the router can surface the real ripgrep exit
        # code on ``command.run`` events.
        self.search_response: tuple[list[dict[str, object]], bool, int, int] = (
            [],
            False,
            0,
            0,
        )
        self.search_error: Exception | None = None
        self.search_calls: list[dict[str, object]] = []
        self.list_calls: int = 0


class _FakeDriver:
    name = "local"

    def __init__(self, state: _FakeDriverState) -> None:
        self.state = state

    async def list_files(self, handle, *, max_files: int = 5000):
        self.state.list_calls += 1
        return list(self.state.list_response[:max_files])

    async def search(
        self,
        handle,
        query: str,
        *,
        glob: str | None,
        case_sensitive: bool,
        regex: bool,
        max_results: int,
    ):
        self.state.search_calls.append(
            {
                "query": query,
                "glob": glob,
                "case_sensitive": case_sensitive,
                "regex": regex,
                "max_results": max_results,
            }
        )
        if self.state.search_error is not None:
            raise self.state.search_error
        return self.state.search_response


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


async def _seed(
    db_engine, *, status: str = "active"
) -> tuple[uuid.UUID, uuid.UUID, _FakeDriverState]:
    from app.db import session as session_module

    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]
    session_module.AsyncSessionLocal = async_sessionmaker(  # type: ignore[assignment]
        bind=db_engine, expire_on_commit=False
    )
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

    return owner_id, session_id, _FakeDriverState()


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


def _make_handle(session_id: uuid.UUID) -> SandboxHandle:
    return SandboxHandle(
        id=f"handle-{session_id}",
        driver="local",
        workdir=Path("/tmp/arena-search-fake"),
        mission_id="m",
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_files_list_returns_filtered_results(client, db_engine) -> None:
    owner_id, session_id, state = await _seed(db_engine)
    state.list_response = [
        "README.md",
        "package.json",
        "src/auth.ts",
        "src/index.ts",
        "src/utils/format.ts",
    ]
    driver = _FakeDriver(state)
    handle = _make_handle(session_id)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)
    _attach_pool(client._transport.app, driver, handle)  # type: ignore[attr-defined]

    resp = await client.get(f"/api/v1/sessions/{session_id}/files/list")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 5
    assert body["truncated"] is False
    assert body["paths"] == state.list_response

    # Filtered call.
    resp_filtered = await client.get(f"/api/v1/sessions/{session_id}/files/list?query=src")
    assert resp_filtered.status_code == 200
    body_filtered = resp_filtered.json()
    assert body_filtered["total"] == 5
    assert all("src" in p for p in body_filtered["paths"])
    assert len(body_filtered["paths"]) == 3


@pytest.mark.asyncio
async def test_files_list_truncates_when_over_max(client, db_engine) -> None:
    owner_id, session_id, state = await _seed(db_engine)
    state.list_response = [f"file-{i:04d}.txt" for i in range(20)]
    driver = _FakeDriver(state)
    handle = _make_handle(session_id)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)
    _attach_pool(client._transport.app, driver, handle)  # type: ignore[attr-defined]

    resp = await client.get(f"/api/v1/sessions/{session_id}/files/list?max=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is True
    assert body["total"] == 20
    assert len(body["paths"]) == 5


@pytest.mark.asyncio
async def test_files_search_happy_path_emits_command_event(client, db_engine) -> None:
    owner_id, session_id, state = await _seed(db_engine)
    state.search_response = (
        [
            {
                "path": "src/auth.ts",
                "line_number": 4,
                "line_text": "export const SECRET = 'abc';",
                "match_start": 13,
                "match_end": 19,
            }
        ],
        False,
        1,
        0,  # exit_code (Phase 4.A.19)
    )
    driver = _FakeDriver(state)
    handle = _make_handle(session_id)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)
    _attach_pool(client._transport.app, driver, handle)  # type: ignore[attr-defined]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/files/search",
        json={"query": "SECRET", "case_sensitive": True},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["truncated"] is False
    assert body["matches"][0]["path"] == "src/auth.ts"
    assert body["matches"][0]["line_number"] == 4
    assert body["duration_ms"] >= 0
    assert state.search_calls[0]["case_sensitive"] is True

    # A command.run supervision event was emitted with category=manual and
    # the search: prefix on the command.
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        events = (
            (
                await db.execute(
                    select(SupervisionEvent).where(
                        SupervisionEvent.session_id == session_id,
                        SupervisionEvent.event_type == "command.run",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        payload = events[0].payload
        assert payload["category"] == "manual"
        assert payload["command"].startswith("search:")
        assert payload["surface"] == "find_in_files"
        assert payload["result_count"] == 1

        # CommandRun row also written so the post-mortem can replay it.
        rows = (
            (await db.execute(select(CommandRun).where(CommandRun.session_id == session_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].category == "manual"


@pytest.mark.asyncio
async def test_files_search_rejects_empty_query(client, db_engine) -> None:
    owner_id, session_id, state = await _seed(db_engine)
    driver = _FakeDriver(state)
    handle = _make_handle(session_id)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)
    _attach_pool(client._transport.app, driver, handle)  # type: ignore[attr-defined]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/files/search",
        json={"query": ""},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 422
    assert state.search_calls == []


@pytest.mark.asyncio
async def test_files_search_rejects_oversized_query(client, db_engine) -> None:
    owner_id, session_id, state = await _seed(db_engine)
    driver = _FakeDriver(state)
    handle = _make_handle(session_id)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)
    _attach_pool(client._transport.app, driver, handle)  # type: ignore[attr-defined]

    big = "x" * 250  # MAX_SEARCH_QUERY == 200
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/files/search",
        json={"query": big},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 422
    assert state.search_calls == []


@pytest.mark.asyncio
async def test_files_search_invalid_regex_returns_400(client, db_engine) -> None:
    owner_id, session_id, state = await _seed(db_engine)
    state.search_error = InvalidRegexError("regex parse error at position 1")
    driver = _FakeDriver(state)
    handle = _make_handle(session_id)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)
    _attach_pool(client._transport.app, driver, handle)  # type: ignore[attr-defined]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/files/search",
        json={"query": "(", "regex": True},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_regex"


@pytest.mark.asyncio
async def test_files_search_timeout_returns_504(client, db_engine) -> None:
    owner_id, session_id, state = await _seed(db_engine)
    state.search_error = SearchTimeoutError("search exceeded 10s budget")
    driver = _FakeDriver(state)
    handle = _make_handle(session_id)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(owner_id))
    client.cookies.set("arena_csrf", _CSRF)
    _attach_pool(client._transport.app, driver, handle)  # type: ignore[attr-defined]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/files/search",
        json={"query": ".*.*.*"},
        headers={"X-CSRF-Token": _CSRF},
    )
    assert resp.status_code == 504
    assert resp.json()["detail"]["code"] == "search_timeout"


@pytest.mark.asyncio
async def test_files_list_requires_ownership(client, db_engine) -> None:
    owner_id, session_id, state = await _seed(db_engine)
    intruder_id = uuid.uuid4()
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        db.add(
            User(
                id=intruder_id,
                email="evil@a.local",
                display_name="E",
                handle="e",
            )
        )
        await db.commit()

    driver = _FakeDriver(state)
    handle = _make_handle(session_id)

    from app.config import get_settings

    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, _cookie(intruder_id))
    client.cookies.set("arena_csrf", _CSRF)
    _attach_pool(client._transport.app, driver, handle)  # type: ignore[attr-defined]

    resp = await client.get(f"/api/v1/sessions/{session_id}/files/list")
    assert resp.status_code == 403
