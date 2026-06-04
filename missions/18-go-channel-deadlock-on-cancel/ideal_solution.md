# Mission 18 — Ideal Solution

Shown in the post-mission report only **after** submit.

## Root cause (in one sentence)

`Submit` does a bare `p.in <- item` on an unbuffered channel; once the
worker's context is cancelled nothing drains that channel, so the send
blocks forever — the cancellation path strands the sender.

## Minimal diff

```diff
--- a/internal/processor/processor.go
+++ b/internal/processor/processor.go
@@ -64,8 +64,15 @@
 // fix is to race the send against ``ctx.Done()`` so a shutting-down
 // pipeline returns the caller's cancellation instead of deadlocking.
 func (p *Processor) Submit(ctx context.Context, item string) error {
-	p.in <- item
-	return nil
+	// Race the send against cancellation: a shutting-down pipeline returns
+	// the caller's context error instead of blocking forever on a channel
+	// no worker is draining.
+	select {
+	case p.in <- item:
+		return nil
+	case <-ctx.Done():
+		return ctx.Err()
+	}
 }
 
 // Wait blocks until the worker has exited (after its Start context is
```

Plus a regression test that pins the property:

```go
// internal/processor/processor_test.go
func TestSubmitAfterCancelReturnsError(t *testing.T) {
    p := processor.New(func(context.Context, string) {})
    ctx, cancel := context.WithCancel(context.Background())
    p.Start(ctx)
    cancel()
    p.Wait()
    // The worker is gone; Submit must return ctx.Err(), not block.
    done := make(chan error, 1)
    go func() { done <- p.Submit(ctx, "x") }()
    select {
    case err := <-done:
        if err == nil {
            t.Fatalf("want cancellation error, got nil")
        }
    case <-time.After(time.Second):
        t.Fatalf("Submit blocked after shutdown")
    }
}
```

Net change: a small `select` plus one regression test — the
`expected_diff_lines_p50` envelope this mission is scored against.

## What the agent got wrong

1. **It traded a deadlock for a goroutine leak.** `go func() { p.in <- item }()`
   unblocks the caller, but the spawned goroutine is now the one stranded
   on the channel. The symptom (caller hangs) disappears; the cause
   (no receiver) remains.
2. **It returns `nil` for an undelivered item.** A caller that gets a
   `nil` error reasonably assumes the item was processed. After shutdown
   it never will be — `Submit` should report the cancellation.

## What a strong supervisor would have prompted

- *"After the worker returns, what reads from `p.in`? Walk me through what
  happens to the goroutine your patch spawns."*
- *"Make `Submit` return `ctx.Err()` when the pipeline is shutting down,
  and add a test that cancels the worker and submits."*

## Validators a careless patch would still trip

- `forbidden_changes.fire_and_forget_send` — fires on
  `go func() { p.in <- item }()`.
- `regression_test_required` — no new test mentions `select` / `ctx.Done`
  / `cancel` / `NumGoroutine`.
