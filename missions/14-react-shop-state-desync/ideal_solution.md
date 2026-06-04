# Mission 14 — Ideal Solution

Shown in the post-mission report only **after** submit. Pairs the minimal
correct diff with a walkthrough of what the agent got wrong and what a
strong supervisor would have caught.

## Root cause (in one sentence)

`addItem` applies the optimistic update (`setItems([...prev, sku])`) and
then `await api.addToCart(sku)`, but the failure path has no rollback — so
when the server rejects the item the optimistically-added sku stays in
`items` and the local cart desyncs from the server.

## Minimal diff

```diff
--- a/frontend/src/useCart.ts
+++ b/frontend/src/useCart.ts
@@ -32,16 +32,21 @@
   const addItem = useCallback(
     async (sku: string) => {
       setError(null);
-      // Snapshot the pre-update cart for reference.
+      // Snapshot the pre-update cart so we can restore it on failure.
       const snapshot = itemsRef.current;
       // Optimistic update: show the item right away.
       const next = [...snapshot, sku];
       itemsRef.current = next;
       setItems(next);
-      // Confirm with the server. If this rejects we do nothing, so the
-      // optimistically-added item stays in the cart even though the server
-      // refused it — the local cart now disagrees with the server.
-      await api.addToCart(sku);
+      try {
+        await api.addToCart(sku);
+      } catch (e) {
+        // Roll back: the server refused the item, so restore the snapshot
+        // and surface the error.
+        itemsRef.current = snapshot;
+        setItems(snapshot);
+        setError(e instanceof Error ? e.message : "could not add item");
+      }
     },
     [api],
   );
```

Plus a regression test (visible suite) that drives a rejecting api and
asserts the optimistic item is rolled back:

```tsx
// frontend/src/tests/unit/useCart.test.tsx
import { act, renderHook, waitFor } from "@testing-library/react";

it("rolls back the optimistic item when the server rejects the add", async () => {
  const api: CartApi = {
    addToCart: vi.fn().mockRejectedValue(new Error("out of stock")),
  };
  const { result } = renderHook(() => useCart(api));

  await act(async () => {
    await result.current.addItem("sku-apple").catch(() => undefined);
  });

  await waitFor(() => {
    expect(result.current.items).not.toContain("sku-apple");
  });
});
```

Net change: snapshot the cart before the optimistic update and restore it
in the `catch`. That's the `expected_diff_lines_p50` envelope this mission
is scored against. The snapshot is read from `itemsRef` (a ref mirroring
the latest committed `items`) so a *second* add rolls back to the correct
prior cart, not an empty one.

## What the agent got wrong

Two red flags a supervisor should catch on the agent's diff:

1. **It handled the rejection but not the state.** Wrapping the await in
   `try/catch` and calling `setError(...)` stops the unhandled rejection,
   but the catch never touches `items` — so the rejected sku is still in
   the cart. The symptom (a stray console error) went away; the actual bug
   (UI showing an item the server refused) did not.
2. **It rationalized skipping the rollback.** The `[skipped]` section of
   `prompts/reasoning.md` says it left the item in to avoid a "flicker" —
   that trade-off is precisely the desync the mission is about. An
   optimistic update that can't be rolled back is just a lie to the user.

## What a strong supervisor would have prompted

- *"When `api.addToCart` rejects, what is in `items`? Drive a rejecting
  mock and check."* — surfaces that the optimistic item is never removed.
- *"Snapshot the cart before the optimistic update and restore it in the
  catch, then add a test for the rejected-add path."* — forces the next
  diff to roll back, not just report.
- *"If a second add is rejected, does it roll back to the right cart?"* —
  pulls the snapshot-source (ref vs. stale closure) into scope.

## Validators a careless patch would still trip

- `regression_test_required` — no new test mentions `rollback` / `rejects`
  / `mockRejected` / `not.toContain`.
- `forbidden_changes.drops_optimistic_update` — fires if the patch removes
  the optimistic `setItems` entirely (awaiting the server first) instead
  of rolling it back.
- `forbidden_changes.hook_rewritten` — fires on a wholesale rewrite of the
  hook rather than the small snapshot + rollback.
