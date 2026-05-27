# Mission 12 — Store Layer Drops the Request Context

**Pack:** `go-orders-service` · **Runtime:** Go 1.22 · **Failure mode:** `context_dropped`

## Author notes

The pack ships with `Store.Get` and `Store.List` that take a
`context.Context` parameter — and then immediately discard it
(`_ = ctx`) before running the query against `context.Background()`.
Visible tests don't exercise cancellation; clean `make test` is green.

## What the agent gets wrong

A naïve coding agent reads `Get()`, sees `context.Background()`, and
decides the right "fix" is to bound the query with a timeout. The
patch replaces `context.Background()` with
`context.WithTimeout(context.Background(), 5*time.Second)`. Background
is **still the parent**; the request's cancellation signal is still
lost. The only difference is queries now self-abort after five seconds.

## Ideal solution

Drop the `_ = ctx` discard and thread the caller's `ctx` directly into
`QueryContext` / `QueryRowContext`. Roughly ~4 added/removed lines.
See `ideal_solution.diff`.

## Hidden tests

- `TestGetPropagatesCancellation` — pre-cancelled ctx, expects
  `errors.Is(err, context.Canceled)`.
- `TestListPropagatesCancellation` — same contract on List.
- `TestGetHonoursRequestContextOverBackground` — cancels a parent ctx,
  asserts the child surfaces it. Catches the agent's
  `WithTimeout(Background, ...)` mis-fix.
