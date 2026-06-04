# 14 — react-shop-state-desync

Author notes (not user-facing). The player sees `mission.yaml.brief`
inside the workspace, not this file.

## Premise

`fullstack-auth-demo` gains `frontend/src/useCart.ts`: a `useCart(api)`
hook returning `{ items, error, addItem }`, plus a minimal
`frontend/src/ShoppingCart.tsx` that renders it. `addItem(sku)` does an
optimistic update (`setItems([...prev, sku])`, mirrored through a
`itemsRef` so callbacks read the latest cart) and then
`await api.addToCart(sku)`. The latent bug: there is no rollback on the
failure path, so when the server rejects an item it stays in `items` and
the local cart desyncs from the server.

The visible unit suite (`frontend/src/tests/unit/useCart.test.tsx`) only
drives a *succeeding* api, so it never exercises the rejected-add path.

## Failure mode (`optimistic_update_no_rollback`)

The agent (`agent_patch.diff`) wraps `await api.addToCart(sku)` in a
try/catch and sets the hook's `error` state — but the catch never removes
the optimistically-added sku. The unhandled rejection goes away and the
visible tests stay green, but a rejected item is still in the cart.

## Ideal

Snapshot the cart (from `itemsRef`) before the optimistic update and, in
the catch, restore it (`setItems(snapshot)`) so a failed add rolls back.
Plus a regression test that drives a rejecting `api.addToCart` and asserts
the sku is gone from `items`.

The snapshot is read from a ref rather than captured inside a `setItems`
updater on purpose: assigning to outer scope from inside a functional
updater is an impure-update anti-pattern that React 19 can invoke with a
stale base, so a *second* rejected add would roll back to `[]` instead of
the correct prior cart. The ref mirror makes the snapshot reliable.

## Files touched in the repo pack (baseline)

- `frontend/src/useCart.ts` — the hook with the latent no-rollback bug
  (committed to the pack baseline; visible-green).
- `frontend/src/ShoppingCart.tsx` — minimal component using the hook.
- `frontend/src/tests/unit/useCart.test.tsx` — visible happy-path test
  (renderHook + act, succeeding api).

## Hidden tests

`hidden_tests/useCart.hidden.test.tsx` (run via `runner.sh`, which copies
it into `frontend/src/tests/hidden/` and runs vitest with the JSON
reporter): two probes — one drives a rejecting api and asserts the sku is
gone from `items`; one confirms a first (accepted) add survives while a
second (rejected) add rolls back to the one-item cart.

## Verified end-to-end

- baseline: visible PASS, hidden FAIL (2/2 fail — no rollback)
- agent_patch: visible PASS, hidden FAIL (2/2 fail — error set, no rollback)
- ideal_solution: visible PASS, hidden PASS (2/2)

## Forbidden changes (modest penalties)

- `hook_rewritten` — >= 60 added lines in useCart.ts (over-reach).
- `drops_optimistic_update` — `setItems` removed from the live file
  (awaiting the server first; a valid-but-different design).
- `silences_with_cast` — added `as any` cast instead of a typed snapshot.
