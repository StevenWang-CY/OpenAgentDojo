# Mission 13 — Ideal Solution

## Root cause (in one sentence)

`lookupOrder` wraps `store.ErrOrderNotFound` with `%v` instead of
`%w` — Go's `errors.Is` cannot walk a `%v` chain, so the handler's
sentinel check at the next layer up always returns false, and the
HTTP response defaults to 500.

## Minimal diff

```diff
--- a/internal/handlers/handlers.go
+++ b/internal/handlers/handlers.go
@@ -110,7 +110,7 @@
 func lookupOrder(ctx context.Context, s *store.Store, id string) (model.Order, error) {
 	o, err := s.Get(ctx, id)
 	if err != nil {
-		return model.Order{}, fmt.Errorf("lookup order %q: %v", id, err)
+		return model.Order{}, fmt.Errorf("lookup order %q: %w", id, err)
 	}
 	return o, nil
 }
```

Plus a regression test that pins the 404 contract:

```go
// internal/handlers/handlers_test.go
func TestGetMissingOrderReturns404(t *testing.T) {
    _, r := newServer(t)
    w := httptest.NewRecorder()
    r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/orders/nope", nil))
    if w.Code != http.StatusNotFound {
        t.Fatalf("got %d want 404", w.Code)
    }
}
```

Net change: **~2 added/removed lines**, one source file plus one
test file. That's the `expected_diff_lines_p50` envelope this
mission is scored against.

## What the agent got wrong

Two red flags a supervisor should catch on the diff:

1. **It never names `%w` / `%v` / `errors.Is`.** The mission's
   failure mode is the verb in the format string. An agent that
   talks about "operator alert clarity" without ever naming the
   verb hasn't actually read the wrap.
2. **The diff only changes the message string.** A reword leaves
   the behaviour unchanged — the API still returns 500, and the
   on-call alert still fires for missing orders.

## What a strong supervisor would have prompted

- *"What does `errors.Is(err, store.ErrOrderNotFound)` return today?
  Walk through the wrap chain in your head."* — surfaces the verb
  without giving away the fix.
- *"Read the Go stdlib docs on `fmt.Errorf`. What's the difference
  between `%v` and `%w`?"* — pulls the right reference into scope.
- *"Add a regression test that hits `GET /orders/{missing}` and
  asserts the status code is 404."* — forces the next diff to
  engage with the HTTP contract.

## Validators a careless patch would still trip

- `forbidden_changes.kept_percent_v` — fires on any patch that
  keeps `%v` in a `fmt.Errorf` call inside the handlers package.
- `regression_test_required` — no new test contains `404` /
  `StatusNotFound` / `ErrOrderNotFound` / `errors.Is`.
- `diff_scope` — fix that wanders into the store or queue package
  exits the scope envelope.
