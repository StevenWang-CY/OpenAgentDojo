"""Test fixtures.

Most tests use SQLite (in-memory async) so they can run on a laptop with no
external services. A handful of integration tests require Postgres and are
marked accordingly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _set_test_env() -> None:
    """Pin env-driven values so import-time settings are stable for tests."""
    os.environ.setdefault("ARENA_ENV", "test")
    os.environ.setdefault("SANDBOX_DRIVER", "local")
    os.environ.setdefault("SANDBOX_WORKDIR", str(Path("/tmp/arena-test-sandboxes")))
    os.environ.setdefault("SANDBOX_MAX_CONCURRENT", "4")
    os.environ.setdefault("SANDBOX_TIMEOUT_SECONDS", "60")
    os.environ.setdefault("SESSION_SECRET", "test-secret-32-chars-min-aaaaaaaa")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///:memory:")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("MISSIONS_ROOT", str(_REPO_ROOT / "missions"))
    os.environ.setdefault("FEATURE_LLM_NARRATION", "false")


_set_test_env()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest_asyncio.fixture
async def db_engine():
    """Function-scoped in-memory SQLite engine with all tables created.

    We hand-create a minimal schema compatible with our SQLAlchemy 2 models —
    skipping the Alembic migration so SQLite (which lacks CITEXT/JSONB) can
    still exercise the model layer.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    from app import models  # noqa: F401 — register models
    from app.db.base import Base

    # Patch types incompatible with SQLite at table-creation time.
    _patch_models_for_sqlite()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


def _patch_models_for_sqlite() -> None:
    """Monkey-patch unsupported column types + server defaults for SQLite.

    This runs once per process — subsequent calls are no-ops because the
    metadata table objects are already mutated.
    """
    from sqlalchemy import JSON, BigInteger, Integer, Text
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
    from sqlalchemy.dialects.postgresql import CITEXT, JSONB

    from app.db.base import Base

    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            elif isinstance(col.type, CITEXT):
                col.type = Text()
            elif isinstance(col.type, PG_ARRAY):
                col.type = JSON()
            # SQLite only auto-increments INTEGER PK; coerce BIGINT PK to INTEGER.
            if col.primary_key and isinstance(col.type, BigInteger):
                col.type = Integer()

            # Strip Postgres-only server defaults like gen_random_uuid() / now().
            sd = col.server_default
            if sd is not None:
                text = str(getattr(sd, "arg", "")).lower()
                if "gen_random_uuid" in text:
                    col.server_default = None
                    # Provide a python-side UUID factory.
                    import uuid as _uuid

                    col.default = _python_default(_uuid.uuid4)
                elif "now()" in text:
                    col.server_default = None
                    from datetime import datetime

                    col.default = _python_default(lambda: datetime.now(UTC))
                elif "array[]::text[]" in text:
                    col.server_default = None
                    col.default = _python_default(list)
                elif text in {"false", "0"}:
                    col.server_default = None
                    col.default = _python_default(lambda: False)
                elif text in {"true", "1"}:
                    col.server_default = None
                    col.default = _python_default(lambda: True)


def _python_default(factory):
    from sqlalchemy.schema import ColumnDefault

    return ColumnDefault(factory)


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    session_local = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_local() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """ASGI in-process client against the FastAPI app."""
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def client_with_db(db_engine) -> AsyncIterator[AsyncClient]:
    """ASGI client whose app shares the in-memory SQLite engine.

    Tests that exercise endpoints touching the DB should use this fixture
    instead of ``client`` — it rebinds ``app.db.session.AsyncSessionLocal``
    to the test engine and creates all tables.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import session as session_module
    from app.main import create_app

    session_module.AsyncSessionLocal = async_sessionmaker(  # type: ignore[assignment]
        bind=db_engine, expire_on_commit=False
    )
    session_module.get_engine.cache_clear()  # type: ignore[attr-defined]

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def sample_mission_yaml(tmp_path: Path) -> Path:
    """Write a minimal valid mission.yaml to a tmp folder and return its path."""
    folder = tmp_path / "01-sample"
    folder.mkdir()
    (folder / "agent_patch.diff").write_text("--- a/foo\n+++ b/foo\n", encoding="utf-8")
    (folder / "response.md").write_text("seed response", encoding="utf-8")
    (folder / "ideal_solution.md").write_text("ideal", encoding="utf-8")
    # P0-2 — every non-tutorial mission must ship ideal_solution.diff so the
    # post-mortem walkthrough can render its three-way comparison.
    (folder / "ideal_solution.diff").write_text(
        "--- a/foo\n+++ b/foo\n@@ -0,0 +1 @@\n+ideal\n",
        encoding="utf-8",
    )
    yaml = """\
id: sample-mission
version: 1
title: Sample
short_description: Short.
difficulty: beginner
category: testing
estimated_minutes: 10
skills_tested: [testing]
repo:
  pack: fullstack-auth-demo
  initial_commit: abc123de
  workdir: /workspace
  language_runtime: node20
  setup_commands: []
  ready_check: "true"
  test_commands:
    unit: "true"
brief: "A short brief."
failure_mode:
  id: overfitted_visible_test
  title: "Test failure mode"
  description: "desc"
tags:
  - overfitted_visible_test
  - lang:typescript
expected_weak_dim: safety
expected_files: [src/index.ts]
expected_context:
  required: [src/a.ts, src/b.ts]
  recommended: [src/c.ts]
  discouraged: [src/d.ts]
agent:
  patch_file: agent_patch.diff
  response_template: response.md
  applies_when:
    prompt_min_chars: 40
    prompt_must_contain_any: [fix]
  apply_mode: on_user_confirm
visible_tests: ["sample test"]
hidden_tests:
  command: "true"
  expected_pass: ["sample"]
validators:
  - kind: diff_scope
    max_files_changed: 4
    max_added_lines: 100
scoring_weights:
  final_correctness: 30
  verification: 15
  agent_review: 15
  prompt_quality: 10
  context_selection: 10
  safety: 10
  diff_minimality: 10
reward_signals:
  prompt_quality:
    must_include_any: [reproduce, root cause, regression]
    bonus_keywords: [security]
    penalty_if_under_chars: 40
  verification:
    required_categories: [test, typecheck]
expected_diff_lines_p50: 18
published: true
"""
    (folder / "mission.yaml").write_text(yaml, encoding="utf-8")
    return folder


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Skip docker tests on hosts without Docker."""
    skip_docker = pytest.mark.skip(reason="docker not available")
    has_docker = False
    try:
        import docker

        client = docker.from_env()  # type: ignore[name-defined]
        client.ping()  # type: ignore[attr-defined]
        has_docker = True
    except Exception:
        has_docker = False
    if has_docker:
        return
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip_docker)
