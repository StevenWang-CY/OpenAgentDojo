"""The app lifespan must start both background pool tasks.

§M8 hardening — ``sandbox-reaper`` (idle handle reaper) AND
``sandbox-orphan-sweeper`` (abandoned DB row sweeper) must be created during
``lifespan()`` and torn down cleanly. Before this guard the sweeper existed
but was never scheduled, so crashed/abandoned sessions stayed ``active``
forever.
"""

from __future__ import annotations

import asyncio

import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_lifespan_starts_reaper_and_orphan_sweeper_tasks() -> None:
    """While the app is inside its lifespan, both named tasks exist on the loop.

    httpx's ``ASGITransport`` does NOT drive ASGI lifespan events, so we exercise
    the FastAPI router's ``lifespan_context`` directly. This is the same code
    path Starlette uses at production startup.
    """
    app = create_app()

    async with app.router.lifespan_context(app):
        task_names = {t.get_name() for t in asyncio.all_tasks() if not t.done()}
        assert "sandbox-reaper" in task_names, (
            f"sandbox-reaper task missing — found: {sorted(task_names)}"
        )
        assert "sandbox-orphan-sweeper" in task_names, (
            f"sandbox-orphan-sweeper task missing — found: {sorted(task_names)}"
        )


@pytest.mark.asyncio
async def test_lifespan_cancels_both_tasks_on_shutdown() -> None:
    """After the lifespan context exits, neither task should remain pending.

    Cancellation must swallow CancelledError exactly once (no traceback escapes
    and the ``async with`` block must exit cleanly).
    """
    app = create_app()

    async with app.router.lifespan_context(app):
        live = {t.get_name() for t in asyncio.all_tasks() if not t.done()}
        assert "sandbox-reaper" in live
        assert "sandbox-orphan-sweeper" in live

    # Give the loop a beat to let cancellation propagate.
    await asyncio.sleep(0)

    remaining = {t.get_name() for t in asyncio.all_tasks() if not t.done()}
    assert "sandbox-reaper" not in remaining
    assert "sandbox-orphan-sweeper" not in remaining
