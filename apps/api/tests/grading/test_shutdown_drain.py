"""Shutdown-drain helpers for in-flight grading tasks (P1-3).

The FastAPI lifespan handler calls :func:`await_active_grading_tasks`
before tearing down the grading FS thread pool so a background grader
gets a chance to commit its submission row + verification envelope.

These tests pin the two guarantees:

* a registered task that finishes BEFORE the deadline counts as
  ``completed`` and is removed from the tracked set;
* a registered task that BLOCKS past the deadline counts as
  ``timed_out`` and is NOT silently dropped — the caller's log line
  surfaces the count so on-call sees the wedge.
"""

from __future__ import annotations

import asyncio

import pytest

from app.grading.runner import (
    active_grading_task_count,
    await_active_grading_tasks,
    register_grading_task,
)


@pytest.mark.asyncio
async def test_drain_awaits_completing_task() -> None:
    """A registered task that finishes within the deadline counts as completed."""

    async def _finishes_quickly() -> str:
        await asyncio.sleep(0)
        return "done"

    task = asyncio.create_task(_finishes_quickly())
    register_grading_task(task)
    assert active_grading_task_count() == 1

    completed, timed_out = await await_active_grading_tasks(deadline_seconds=1.0)
    assert (completed, timed_out) == (1, 0)
    # done_callback fires synchronously on the loop after the task
    # resolves; we yield once to let it run.
    await asyncio.sleep(0)
    assert active_grading_task_count() == 0


@pytest.mark.asyncio
async def test_drain_surfaces_timed_out_task() -> None:
    """A registered task still running past the deadline is reported, not dropped."""

    blocker = asyncio.Event()

    async def _blocks_until_signalled() -> None:
        await blocker.wait()

    task = asyncio.create_task(_blocks_until_signalled())
    register_grading_task(task)

    completed, timed_out = await await_active_grading_tasks(deadline_seconds=0.1)
    assert (completed, timed_out) == (0, 1)
    assert active_grading_task_count() == 1

    # Clean up so the task doesn't leak into other tests.
    blocker.set()
    await task
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_drain_with_no_registered_tasks_is_noop() -> None:
    """Empty tracker → (0, 0) immediately, no awaits, no exceptions."""
    assert active_grading_task_count() == 0
    result = await await_active_grading_tasks(deadline_seconds=0.0)
    assert result == (0, 0)
