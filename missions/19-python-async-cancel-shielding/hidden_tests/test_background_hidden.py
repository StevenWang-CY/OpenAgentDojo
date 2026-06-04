"""Hidden tests for Mission 19 — asyncio.shield misuse.

The visible suite runs ``run_request`` to completion, where the side effect
should appear. These hidden tests cancel ``run_request`` *mid-flight* (while
the worker is still sleeping) and then wait past the worker's delay before
asserting the side effect was NOT recorded.

The wait is load-bearing: ``asyncio.shield`` lets the cancelled awaiter
return immediately while the shielded worker keeps running in the
background. A test that asserts straight after the cancellation would miss
the leak — the worker appends ``"done"`` a moment later. Waiting past
``WORKER_DELAY_S`` gives a shielded worker time to record its ghost write,
so a shield is caught.
"""

from __future__ import annotations

import asyncio

import pytest

from app.background import WORKER_DELAY_S, run_request


async def test_cancellation_suppresses_side_effect() -> None:
    """Cancelling mid-flight must leave no 'done' side effect, even after the
    worker's delay would have elapsed."""
    side_effects: list[str] = []
    task = asyncio.create_task(run_request(side_effects))
    # Let the worker reach its sleep, then cancel before it can append.
    await asyncio.sleep(WORKER_DELAY_S / 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Wait well past the worker's delay: a shielded worker would record its
    # side effect in this window; a correctly-cancelled one never does.
    await asyncio.sleep(WORKER_DELAY_S * 3)
    assert side_effects == [], (
        "cancelled request still recorded its side effect — the worker was "
        "shielded from cancellation"
    )


async def test_cancellation_ends_the_task_cancelled() -> None:
    """The task must actually end cancelled, not swallow the cancellation."""
    side_effects: list[str] = []
    task = asyncio.create_task(run_request(side_effects))
    await asyncio.sleep(WORKER_DELAY_S / 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_side_effect_still_appears_without_cancellation() -> None:
    """Sanity: with no cancellation the side effect is recorded exactly once."""
    side_effects: list[str] = []
    await run_request(side_effects)
    assert side_effects == ["done"]
