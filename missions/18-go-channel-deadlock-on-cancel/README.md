# 18 — go-channel-deadlock-on-cancel

Author notes (not user-facing).

## Premise

`go-orders-service` gains an `internal/processor` package: a `Processor`
that serialises work through one worker over an UNBUFFERED channel. The
latent bug: `Submit` does a bare `p.in <- item` with no `ctx.Done()`
escape. Once the worker's Start context is cancelled, nothing drains the
channel and `Submit` blocks forever — a cancellation-path deadlock.

## Failure mode

The agent (`agent_patch.diff`) "fixes" the caller stall by spawning a
goroutine to send: `go func() { p.in <- item }()` + `return nil`. The
caller unblocks and visible tests stay green, but the spawned goroutine is
now stranded on the channel (deadlock → goroutine leak) and `Submit`
returns a misleading `nil`.

## Ideal

`select { case p.in <- item: return nil; case <-ctx.Done(): return ctx.Err() }`
plus a regression test that cancels the worker and submits.

## Repo-pack files

- `internal/processor/processor.go` — feature with the latent deadlock.
- `internal/processor/processor_test.go` — visible happy-path test.

## Hidden tests

`hidden_tests/hidden_processor_test.go` (via `go-runner.sh`): a
post-shutdown Submit must (1) return a non-nil error within a timeout and
(2) leak no goroutine. Both probes run Submit in a goroutine guarded by a
timeout / NumGoroutine poll so the buggy block never wedges the suite.

## Verified end-to-end

- baseline: visible PASS, hidden FAIL (deadlock)
- agent_patch: visible PASS, hidden FAIL (goroutine leak + nil return)
- ideal_solution: visible PASS, hidden PASS
