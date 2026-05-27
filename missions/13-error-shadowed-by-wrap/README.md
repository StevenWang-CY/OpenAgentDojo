# Mission 13 — errors.Is Shadowed by an Over-Eager Wrap

**Pack:** `go-orders-service` · **Runtime:** Go 1.22 · **Failure mode:** `error_wrapped_swallowed`

## Author notes

The pack ships with `lookupOrder` wrapping `store.ErrOrderNotFound`
via `fmt.Errorf("lookup order %q: %v", id, err)`. The `%v` verb
formats the wrapped error as a string and drops the type — so the
handler's `errors.Is(err, store.ErrOrderNotFound)` check returns
false, and the API answers 500 instead of 404.

## What the agent gets wrong

A naïve agent reads the wrap, decides the message text is the
problem, and reword it ("failed to look up order…") while keeping
`%v`. The body changes; the status code doesn't.

## Ideal solution

Change `%v` → `%w` in `lookupOrder`. One character. See
`ideal_solution.diff`. Add a regression test that hits
`/orders/{missing}` and asserts the 404.

## Hidden tests

- `TestGetMissingOrderReturns404` — 404 contract on `GET`.
- `TestShipMissingOrderReturns404` — same on `POST .../ship`.
- `TestLookupOrderPreservesSentinel` — multiple missing-id paths,
  each asserting 404. Catches the agent's reword-only patch.
