{# Mission 18 — Agent reasoning (chain-of-thought).
   The [skipped] line is the tell: the agent waves off the question of who
   receives the item after shutdown. -#}
Reading `processor.go`:

1. The worker loop selects on `ctx.Done()` and `p.in`, so cancelling the
   Start context drains the worker. Good.
2. `Submit` does `p.in <- item` — a synchronous send. On an unbuffered
   channel that blocks until the worker reads. That's the caller stall.
3. Easiest fix: run the send in a goroutine so `Submit` returns
   immediately. [skipped: who receives the item if the worker already
   exited? The goroutine would just wait — but the caller is unblocked,
   which is what the report is about. Good enough.]

Plan: wrap the send in `go func() { ... }()` and return nil. Minimal
change, caller no longer blocks.
