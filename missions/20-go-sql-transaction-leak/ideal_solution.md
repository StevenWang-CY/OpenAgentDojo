# Mission 20 — Ideal Solution

Shown in the post-mission report only **after** submit. Pairs the minimal
correct diff with a walkthrough of what the agent got wrong and what a
strong supervisor would have caught.

## Root cause (in one sentence)

`BulkUpdateStatus` opens a transaction with `s.db.BeginTx` but only the
final `Commit` releases it — the three early `return` statements (two SQL
errors and the `ErrOrderNotFound` unknown-id path) walk away with the
transaction still open, and because the pool is pinned to a single
connection (`db.SetMaxOpenConns(1)` in `Open`), one leaked transaction
strands the whole service.

## Minimal diff

```diff
--- a/internal/store/bulk.go
+++ b/internal/store/bulk.go
@@ -28,6 +28,10 @@
 	if err != nil {
 		return fmt.Errorf("begin bulk update: %w", err)
 	}
+	// Roll back on every return path. After a successful Commit this is a
+	// no-op (the tx is already done); on any early return it releases the
+	// pooled connection the transaction is holding.
+	defer func() { _ = tx.Rollback() }()
 	for _, id := range ids {
 		res, err := tx.ExecContext(ctx,
 			`UPDATE orders SET status = ? WHERE id = ?`, string(status), id)
```

Plus a regression test that rejects a batch with an unknown id and then
proves the store is still usable (the connection was returned):

```go
// internal/store/bulk_test.go
func TestBulkUpdateRollsBackUnknownID(t *testing.T) {
    s := newStore(t)
    seed(t, s, "a")
    if err := s.BulkUpdateStatus(ctx, []string{"a", "missing"}, model.StatusShipped); !errors.Is(err, store.ErrOrderNotFound) {
        t.Fatalf("want ErrOrderNotFound, got %v", err)
    }
    // The connection must be back in the pool: a follow-up call must not
    // block, and "a" must be rolled back to pending.
    got, err := s.Get(ctx, "a")
    if err != nil || got.Status != model.StatusPending {
        t.Fatalf("transaction leaked / not rolled back: %v %q", err, got.Status)
    }
}
```

Net change: **4 added lines** in one source file, plus one regression
test. That's the `expected_diff_lines_p50` envelope this mission is scored
against. A single `defer func() { _ = tx.Rollback() }()` immediately after
`BeginTx` covers every return path — including the `Commit` path, where
the deferred rollback is a harmless no-op.

## What the agent got wrong

Two red flags a supervisor should catch on the agent's diff:

1. **It only rolls back on the SQL-error branches.** The agent added
   `tx.Rollback()` inside the two `if err != nil` blocks but left the
   `if n == 0 { return ErrOrderNotFound }` path untouched — and the
   unknown-id path is exactly the one operators trigger with a typo'd id.
   The leak survives on the path that matters.
2. **It never reuses the store after a failure.** The agent's
   verification only re-runs the happy-path test. A regression test that
   issues a *second* batch (or a follow-up read) after a rejected batch
   would have surfaced the stranded connection immediately.

## What a strong supervisor would have prompted

- *"List every `return` in `BulkUpdateStatus` and tell me which ones run
  with the transaction still open."* — surfaces the missing rollback on
  the unknown-id path.
- *"Write a regression test that rejects a batch with an unknown id and
  then performs another DB call on the same store."* — forces the next
  diff to engage with connection reuse, not just the error string.
- *"Why is one leaked transaction enough to wedge the whole service? Read
  `Open`."* — pulls `SetMaxOpenConns(1)` into scope.

## Validators a careless patch would still trip

- `diff_scope` — touching only `internal/store/bulk.go` with no test
  delta triggers the "missing regression test" validator.
- `forbidden_changes.masked_with_more_connections` — fires on any patch
  that widens `SetMaxOpenConns` instead of releasing the transaction.
- `regression_test_required` — no new test mentions `Rollback` /
  `transaction` / `ErrOrderNotFound`.
