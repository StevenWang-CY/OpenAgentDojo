# Mission 19 — Ideal Solution

Shown in the post-mission report only **after** submit. Pairs the minimal
correct diff with a walkthrough of what the agent got wrong and what a
strong supervisor would have caught.

## Root cause (in one sentence)

Asked to make the background work resilient, the agent wraps the worker in
`asyncio.shield`, which decouples it from the caller's cancellation — so a
cancelled `run_request` returns immediately while the shielded worker keeps
running and records its side effect a moment later, a ghost write the
cancellation contract forbids and the no-cancellation visible suite never
sees.

## Minimal diff

```diff
--- a/backend/app/background.py
+++ b/backend/app/background.py
@@ -28,6 +28,7 @@
 from __future__ import annotations
 
 import asyncio
+import contextlib
 
 #: How long the worker sleeps before recording its side effect.
 WORKER_DELAY_S: float = 0.05
@@ -46,4 +47,15 @@
         await asyncio.sleep(WORKER_DELAY_S)
         side_effects.append("done")
 
-    await worker()
+    # Run the worker as a tracked task so a cancellation of run_request can
+    # propagate into it: cancel and await the task in the finally block so it
+    # is never left dangling, and the side effect is only recorded when the
+    # request actually ran to completion.
+    task = asyncio.ensure_future(worker())
+    try:
+        await task
+    finally:
+        if not task.done():
+            task.cancel()
+            with contextlib.suppress(asyncio.CancelledError):
+                await task
```

The fix runs the worker as a *tracked task* and cancels it on cleanup, so a
cancellation of `run_request` propagates into the worker (the side effect is
only recorded when the request actually completes) and the worker is never
left running in the background. Dropping the shield and `await`ing the worker
directly is also acceptable; the tracked-task form additionally guarantees
the worker is torn down on any early exit.

Plus a regression test that cancels `run_request` mid-flight and asserts no
side effect:

```python
# backend/tests/unit/test_background.py
import asyncio
import pytest
from app.background import WORKER_DELAY_S, run_request

async def test_cancelled_request_records_no_side_effect():
    side_effects: list[str] = []
    task = asyncio.create_task(run_request(side_effects))
    await asyncio.sleep(WORKER_DELAY_S / 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(WORKER_DELAY_S * 3)  # past the worker's delay
    assert side_effects == []
```

Net change: run the worker as a tracked task with a cancel-on-cleanup
`finally`, plus one regression test. That's the `expected_diff_lines_p50`
envelope this mission is scored against.

## What the agent got wrong

Two red flags a supervisor should catch on the agent's diff:

1. **It shielded state-mutating work from cancellation.** `asyncio.shield`
   is for work that *must* outlive its caller (a flush, an ack). A worker
   that records a side effect on behalf of a request must honour the
   request's cancellation — shielding it turns a cancelled request into a
   ghost write.
2. **It never tested the cancellation path.** The agent's verification only
   re-runs the no-cancellation visible suite. A regression test that cancels
   `run_request` mid-flight and waits past the worker's delay would have
   surfaced the leaked side effect immediately.

## What a strong supervisor would have prompted

- *"Start `run_request` as a task, cancel it during the sleep, then check
  whether `'done'` was appended. Walk me through what `shield` does there."*
- *"A cancelled request must not mutate state. Let the cancellation reach the
  worker instead of shielding it."*
- *"Add a regression test that cancels mid-flight and asserts no side effect
  — and make sure it waits past the worker's delay so a shielded write is
  caught."*

## Validators a careless patch would still trip

- `forbidden_changes.shields_the_worker` — fires on any patch containing
  `asyncio.shield`.
- `regression_test_required` — no new test mentioning `cancel` /
  `cancellation` / `shield` / `run_request`.
- Hidden test "cancelling mid-flight records no side effect" fails for any
  patch that shields the worker.
