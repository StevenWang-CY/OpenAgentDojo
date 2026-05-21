"""Per-validator timeout (P0-B3).

A hung validator must not stall the whole grading budget. We wrap each
``dispatch(...)`` call in ``asyncio.wait_for(..., timeout=30s)`` and surface
the timeout as a ``ValidatorResult`` with ``passed=False`` and a clear
``violations`` message.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.grading.validators import ValidatorResult


def _slow_dispatch(config, ctx):
    """Stand-in dispatch that sleeps past the (shrunken) timeout."""
    time.sleep(0.5)
    return ValidatorResult(kind="forbidden_changes", passed=True, violations=[])


@pytest.mark.asyncio
async def test_validator_timeout_surfaces_as_failure() -> None:
    """A validator that hangs past the timeout returns a fail with a clear msg."""
    # Mirror the runner's per-validator dispatch with a shrunken timeout so
    # the test is fast. We exercise the wait_for + to_thread combo directly
    # to isolate the timeout behaviour from DB / driver wiring.
    timeout_s = 0.1

    async def _dispatch_one(config: dict) -> ValidatorResult:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_slow_dispatch, config, {}),
                timeout=timeout_s,
            )
        except TimeoutError:
            return ValidatorResult(
                kind=str(config.get("kind", "unknown")),
                passed=False,
                violations=[f"validator timed out after {timeout_s}s"],
            )

    result = await _dispatch_one({"kind": "forbidden_changes"})
    assert result.passed is False
    assert result.violations
    assert "timed out" in result.violations[0]


def test_runner_has_per_validator_timeout_constant() -> None:
    """Guard against accidental removal of the timeout knob."""
    from app.grading.runner import PER_VALIDATOR_TIMEOUT_S

    assert PER_VALIDATOR_TIMEOUT_S > 0
    assert PER_VALIDATOR_TIMEOUT_S <= 60  # sanity bound
