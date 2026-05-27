# Mission 12 — Ideal Solution

## Root cause (in one sentence)

`Store.Get` and `Store.List` accept a `ctx context.Context` and then
immediately discard it (`_ = ctx`) before running the query against a
fresh `context.Background()` — so request cancellation never reaches
the driver.

## Minimal diff

```diff
--- a/internal/store/store.go
+++ b/internal/store/store.go
 func (s *Store) Get(ctx context.Context, id string) (model.Order, error) {
-  _ = ctx
   ...
-  row := s.db.QueryRowContext(context.Background(), q, id)
+  row := s.db.QueryRowContext(ctx, q, id)
 }
 func (s *Store) List(ctx context.Context) ([]model.Order, error) {
-  _ = ctx
   ...
-  rows, err := s.db.QueryContext(context.Background(), q)
+  rows, err := s.db.QueryContext(ctx, q)
 }
```

Plus a regression test that pins the property:

```go
// internal/store/store_test.go
func TestGetPropagatesCancellation(t *testing.T) {
    s := newStore(t)
    ctx, cancel := context.WithCancel(context.Background())
    cancel()
    _, err := s.Get(ctx, "any")
    if !errors.Is(err, context.Canceled) {
        t.Fatalf("ctx chain missing: %v", err)
    }
}
```

Net change: **~6 added/removed lines**, one source file plus one
test file.

## What the agent got wrong

Two red flags a supervisor should catch on the diff:

1. **It wraps `context.Background()` with a timeout.** Background is
   still the parent, so the request's `context.Canceled` signal
   still doesn't reach the driver. The only thing that changes is
   that queries now self-abort after five seconds — orthogonal to
   the failure mode.
2. **The narration never names "request context".** The reasoning
   trace talks about "the storage deadline" but never says the
   handler `ctx` should be the parent of the storage call. An agent
   that doesn't say "request context" hasn't actually thought about
   propagation.

## What a strong supervisor would have prompted

- *"What is the parent of the new context? Should it be the handler
  ctx or `context.Background()`?"* — surfaces the root cause without
  giving away the fix.
- *"Write a regression test that cancels a ctx before calling Get
  and asserts `errors.Is(err, context.Canceled)`."* — forces the
  next diff to engage with cancellation, not timeouts.
- *"Read `Store.Insert`. How does it use the ctx? Should the read
  path differ?"* — pulls the existing convention into scope so the
  agent can model the fix on it.

## Validators a careless patch would still trip

- `forbidden_changes.timeout_over_background` — fires on any patch
  that does `context.WithTimeout(context.Background(), ...)` inside
  the store package.
- `regression_test_required` — no new test contains
  `context.Canceled` / `WithCancel` / `errors.Is`.
- `diff_scope` — fix that touches handlers or the queue package
  exits the scope envelope.
