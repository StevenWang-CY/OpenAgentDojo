# Mission 11 — Goroutine Leak on Shutdown

**Pack:** `go-orders-service` · **Runtime:** Go 1.22 · **Failure mode:** `goroutine_leak`

## Author notes

The pack ships with a `queue.Pool` whose `Stop()` is intentionally
broken: it flips the `stopped` flag and returns without cancelling
worker goroutines or waiting for them to exit. The visible test suite
covers the *functional* behaviour (events get processed, double-Stop
is safe), so a clean `make test` is misleadingly green.

## What the agent gets wrong

A naïve coding agent reads `Stop()`, sees the `// BUG:` comment, and
decides "fix the bug" means "make the shutdown observable". It adds a
`defer log.Println("queue: stop signalled")` and walks away. The log
line gives the patch a confident shape, but the leak is unchanged.

## Ideal solution (one of several)

Call `p.cancel()` after flipping `p.stopped`, then `p.wg.Wait()` to
drain. Roughly ~6 added lines. See `ideal_solution.diff` for the
canonical shape.

## Hidden tests

- `TestStopReclaimsEveryGoroutine` — exercises `runtime.NumGoroutine()`
  before/after a Stop with 4 workers.
- `TestStopHonoursParentCancellation` — cancels the parent context and
  asserts the workers drain without an explicit Stop.
- `TestStopUnblocksOnBufferedEvents` — fills the events channel and
  asserts Stop still returns promptly.
