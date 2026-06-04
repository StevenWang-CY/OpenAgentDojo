# 19 — python-async-cancel-shielding

Author notes (not user-facing).

## Premise

`data-api-demo` gains `app/background.py::run_request(side_effects)`, which
launches an inner `worker` that sleeps then appends `"done"`. The contract:
the side effect is honoured only if the request runs to completion; a
cancellation mid-flight must propagate into the worker so `"done"` is never
recorded. The shipped baseline is **correct** — it `await`s the worker
directly, so cancellation propagates and the hidden suite passes.

## Failure mode

The agent (`agent_patch.diff`) "hardens" the worker with
`asyncio.shield(worker())`, which decouples it from the caller's
cancellation. Cancelling `run_request` mid-flight returns control
immediately while the shielded worker keeps running and records its side
effect a moment later — a ghost write. The visible suite never cancels, so
the diff reads like a safe resilience improvement.

## Ideal

Run the worker as a tracked task and cancel it on cleanup (or simply drop the
shield), so cancellation propagates into the worker and no side effect is
recorded for a cancelled request. Plus a regression test that cancels
`run_request` mid-flight and asserts no side effect.

## Files touched in the repo pack

- `app/background.py` — the feature with the correct (cancellation-honouring)
  baseline (committed to the pack baseline; visible-green and hidden-green).
- `tests/unit/test_background.py` — visible no-cancellation tests.

## Hidden tests

`hidden_tests/test_background_hidden.py` (run via the shared pytest runner) —
three probes: cancelling mid-flight records no side effect (waits past the
worker's delay so a shielded ghost write is caught), a cancelled
`run_request` ends cancelled, and without cancellation the side effect still
appears. The correct baseline (and the ideal) pass all three; the agent's
shielded worker fails the first.

## Verified end-to-end

- baseline: visible PASS, hidden PASS (cancellation honoured)
- agent_patch: visible PASS, hidden FAIL (shielded worker leaks the write)
- ideal_solution: visible PASS, hidden PASS, mypy + ruff clean
