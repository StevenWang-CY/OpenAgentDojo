# 20 — go-sql-transaction-leak

Author notes (not user-facing).

## Premise

`go-orders-service` gains a `store.BulkUpdateStatus(ctx, ids, status)`
method that updates several orders in one transaction. The latent bug: it
only releases the transaction on the `Commit` (happy) path. Each early
`return` — the two `if err != nil` branches and the
`if n == 0 { return ErrOrderNotFound }` unknown-id path — abandons the
open transaction. Because `store.Open` pins the pool to one connection
(`db.SetMaxOpenConns(1)`), a single leaked transaction stalls every later
query.

## Failure mode

The agent (`agent_patch.diff`) correctly spots the missing rollback but
adds `tx.Rollback()` **only** on the two SQL-error branches, leaving the
`ErrOrderNotFound` early return — the path operators actually hit — still
leaking. Visible tests stay green (happy path commits), so the diff looks
complete.

## Ideal

A single `defer func() { _ = tx.Rollback() }()` right after `BeginTx`
covers every return path (a no-op after `Commit`), plus a regression test
that reuses the store after a rejected batch.

## Files touched in the repo pack

- `internal/store/bulk.go` — the feature with the latent leak (committed
  to the pack baseline; visible-green).
- `internal/store/bulk_test.go` — visible happy-path test.

## Hidden tests

`hidden_tests/hidden_bulk_test.go` (run via the shared `go-runner.sh`
bridge) — three probes that each reject a batch with an unknown id and
then prove the connection was returned, using a goroutine + `time.After`
guard (the store's Get/List ignore their context, and the pool is size 1,
so a leak blocks forever). Each test opens its own on-disk DB and closes
it in the background so a stranded connection cannot wedge the suite.

## Verified end-to-end

- baseline: visible PASS, hidden FAIL (leak)
- agent_patch: visible PASS, hidden FAIL (unknown-id path still leaks)
- ideal_solution: visible PASS, hidden PASS
