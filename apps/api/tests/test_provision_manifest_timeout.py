"""Provisioning worker: manifest load is wall-clock-bounded (P1-B8).

A pathological manifest file (giant YAML, malformed alias chain, broken
filesystem) could stall the sync ``_load_manifest_for`` call indefinitely,
leaving the session row in ``provisioning`` state until the idle reaper
kicks in. We wrap the load in ``asyncio.wait_for`` so the worker can
interrupt the load AND surface a clear ``session.provision_failed`` event
the FE can act on.
"""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.mission import Mission
from app.models.session import SessionRow
from app.models.supervision_event import SupervisionEvent
from app.models.user import User


async def _seed_session(factory, mission_id: str) -> uuid.UUID:
    async with factory() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"prov-{uuid.uuid4().hex[:8]}@arena.local",
            display_name="Prov",
        )
        db.add(user)
        db.add(
            Mission(
                id=mission_id,
                title="Prov",
                difficulty="beginner",
                category="testing",
                repo_pack="pack",
                initial_commit="abc",
                estimated_minutes=5,
                failure_mode="none",
                skills_tested=[],
                manifest_sha256="0" * 64,
                version=1,
                published=True,
            )
        )
        sid = uuid.uuid4()
        db.add(
            SessionRow(
                id=sid,
                user_id=user.id,
                mission_id=mission_id,
                status="provisioning",
            )
        )
        await db.commit()
        return sid


@pytest.mark.asyncio
async def test_manifest_load_timeout_marks_session_error_and_emits_event(
    db_engine, monkeypatch
) -> None:
    from app.db import session as session_module
    from app.workers import provision as provision_module

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    # Re-bind both the module-level sessionmaker AND ``_async_run_provision``
    # transitively imports of ``AsyncSessionLocal`` (the function does an
    # in-body ``from app.db.session import AsyncSessionLocal`` style import
    # via the local module reference — confirmed by reading provision.py).
    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = factory  # type: ignore[assignment]

    try:
        mission_id = "prov-timeout-mission"
        sid = await _seed_session(factory, mission_id)

        # Force the manifest loader to block past the test budget so the
        # wait_for fires. The loader is sync and runs under ``to_thread`` so
        # a plain ``time.sleep`` is the realistic stand-in.
        def _slow_loader(_root, _mid: str):
            time.sleep(0.5)  # > the 0.05 budget below
            return None

        monkeypatch.setattr(provision_module, "_load_manifest_for", _slow_loader)

        # Shrink the timeout so the test finishes promptly. The override
        # attribute is the documented escape hatch for exactly this case.
        monkeypatch.setattr(
            provision_module,
            "_MANIFEST_LOAD_TIMEOUT_S_OVERRIDE",
            0.05,
        )

        # Avoid the side trip through Redis — irrelevant to the timeout path
        # and would otherwise emit a connection warning into the test logs.
        # ``get_redis`` is imported lazily inside the worker; patch the
        # source module so the lazy import resolves to our stub.
        from app.sessions import events as events_module

        async def _no_redis():
            return None

        monkeypatch.setattr(events_module, "get_redis", _no_redis)

        await provision_module._async_run_provision(sid, in_process=False)

        # Session must be marked error.
        async with factory() as db:
            refreshed = await db.get(SessionRow, sid)
            assert refreshed.status == "error", refreshed.status

            rows = (
                (
                    await db.execute(
                        select(SupervisionEvent).where(
                            SupervisionEvent.session_id == sid,
                            SupervisionEvent.event_type == "session.provision_failed",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1, [r.event_type for r in rows]
            payload = rows[0].payload or {}
            assert payload.get("reason") == "manifest_load_timeout"
            assert "0.05" in payload.get("detail", "")
    finally:
        session_module.AsyncSessionLocal = original  # type: ignore[assignment]
