# Mission 11 — Ideal Solution

Shown in the post-mission report only **after** submit. Pairs the
minimal correct diff with a narrative walkthrough of what the agent
got wrong and what a strong supervisor would have caught.

## Root cause (in one sentence)

`Pool.Stop()` flips the `stopped` flag and returns without calling
the `context.CancelFunc` saved during `Start()` and without waiting
on the `sync.WaitGroup` — so every worker stays blocked on the
events channel for the lifetime of the process.

## Minimal diff

```diff
--- a/internal/queue/queue.go
+++ b/internal/queue/queue.go
@@ Stop()
   p.stopped = true
+  cancel := p.cancel
   p.mu.Unlock()
-  // BUG: no p.cancel() call here, no p.wg.Wait().
+  if cancel != nil {
+    cancel()
+  }
+  p.wg.Wait()
 }
```

Plus a regression test that pins the property:

```go
// internal/queue/queue_test.go
func TestStopReclaimsEveryGoroutine(t *testing.T) {
    baseline := runtime.NumGoroutine()
    p, _ := newPool(t, 4)
    p.Start(context.Background())
    time.Sleep(20 * time.Millisecond)

    p.Stop()
    // After Stop returns the live goroutine count must collapse back.
    for i := 0; i < 10; i++ {
        if runtime.NumGoroutine() <= baseline+1 {
            return
        }
        time.Sleep(10 * time.Millisecond)
    }
    t.Fatalf("goroutine leak: baseline=%d now=%d",
        baseline, runtime.NumGoroutine())
}
```

Net change: **~10 added lines**, one source file plus one test file.
That's the `expected_diff_lines_p50` envelope this mission is scored
against.

## What the agent got wrong

Two red flags a supervisor should catch on the agent's diff:

1. **It never names `p.cancel` or `p.wg.Wait`.** The mission's failure
   mode is the missing cancellation. An agent that doesn't mention
   either helper hasn't actually traced the worker lifecycle through
   `Start` → `run` → `Stop`. The `[skipped]` section in
   `prompts/reasoning.md` literally says "calling cancel here would be
   redundant" — that should land as a supervision warning.
2. **The diff only adds a log line.** A `defer log.Println` inside
   Stop is observable from operator dashboards but does nothing to
   reclaim the goroutines. The fix is purely about *visibility*, not
   *correctness* — that's the canonical "fix the symptom not the
   cause" anti-pattern.

## What a strong supervisor would have prompted

- *"Where is `p.cancel` invoked? Read the function and tell me which
  callers reach for it."* — surfaces the dangling cancellation handle.
- *"Write a regression test that calls `runtime.NumGoroutine()` before
  and after Stop and asserts the difference is at most one."* — forces
  the agent's next diff to engage with leak detection.
- *"What happens if `Stop` is called while a worker is mid-process?
  Walk me through the select branches."* — pulls the cancellation
  chain into scope.

## Validators a careless patch would still trip

- `diff_scope` — touching only `internal/queue/queue.go` with no test
  delta triggers the "missing regression test" validator.
- `forbidden_changes.introduced_log_only_fix` — fires on any patch
  that adds `defer log.` inside the queue package without a paired
  `cancel()` call.
- `regression_test_required` — no new test contains `NumGoroutine` /
  `leak` / `cancel` / `WaitGroup`.
