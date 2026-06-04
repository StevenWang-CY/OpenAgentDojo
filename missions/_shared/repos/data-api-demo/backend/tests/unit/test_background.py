"""Visible unit tests for cancellable background work (Mission 19).

These run ``run_request`` to completion (no cancellation), where the side
effect should be recorded. The mid-flight cancellation case that catches a
shield leak lives in the hidden suite.
"""

from __future__ import annotations

from app.background import run_request


async def test_run_request_records_side_effect_on_completion() -> None:
    side_effects: list[str] = []
    await run_request(side_effects)
    assert side_effects == ["done"]


async def test_run_request_appends_once_per_call() -> None:
    side_effects: list[str] = []
    await run_request(side_effects)
    await run_request(side_effects)
    assert side_effects == ["done", "done"]
