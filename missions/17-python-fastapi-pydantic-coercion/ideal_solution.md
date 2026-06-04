# Mission 17 — Ideal Solution

Shown in the post-mission report only **after** submit. Pairs the minimal
correct diff with a walkthrough of what the agent got wrong and what a
strong supervisor would have caught.

## Root cause (in one sentence)

`place_order` and `quote_order` carry the same validate-then-check block, and
the agent "DRYs" the duplication by stripping `quote_order` down to a raw
`int(payload["qty"])` read — which drops the model's coercion guarantees and
the `qty > 0` rule, so a quote silently diverges from the matching placement
on inputs the integer-only visible suite never feeds.

## Minimal diff

```diff
--- a/backend/app/orders_api.py
+++ b/backend/app/orders_api.py
@@ -42,10 +42,11 @@
     qty: int
 
 
-def place_order(payload: dict[str, object]) -> int:
-    """Validate ``payload`` through ``OrderPayload`` and return the total.
+def _line_total(payload: dict[str, object]) -> int:
+    """Validate ``payload`` through ``OrderPayload`` and return its total.
 
-    Coerces ``qty`` to ``int`` via the model and enforces ``qty > 0``.
+    Single source of truth for coercion + the ``qty > 0`` rule, shared by
+    both ``place_order`` and ``quote_order`` so they can never diverge.
 
     Raises:
         ValueError: if ``qty`` is not a positive integer (after coercion).
@@ -56,15 +57,15 @@
     return order.qty * UNIT_PRICE
 
 
+def place_order(payload: dict[str, object]) -> int:
+    """Validate ``payload`` through ``OrderPayload`` and return the total."""
+    return _line_total(payload)
+
+
 def quote_order(payload: dict[str, object]) -> int:
     """Return the line total for ``payload`` without persisting it.
 
-    Runs the identical validation as ``place_order`` (same coercion, same
-    ``qty > 0`` rule) so a quote always matches the matching placement. The
-    block below is duplicated verbatim from ``place_order`` — that is the
-    smell, not a behaviour difference.
+    Shares ``_line_total`` with ``place_order`` so a quote always matches the
+    matching placement: same coercion, same ``qty > 0`` rule.
     """
-    order = OrderPayload.model_validate(payload)
-    if order.qty <= 0:
-        raise ValueError(f"qty must be > 0, got {order.qty}")
-    return order.qty * UNIT_PRICE
+    return _line_total(payload)
```

The fix removes the duplication the *right* way: extract a single
`_line_total` helper that runs `OrderPayload.model_validate` and the
`qty > 0` check once, and have **both** `place_order` and `quote_order`
delegate to it. There is now exactly one coercion + validation path, so the
two can never diverge.

Plus a regression test that covers string coercion **and** non-positive
rejection:

```python
# backend/tests/unit/test_orders_api.py
import pytest
from app.orders_api import place_order, quote_order

def test_quote_matches_placement_for_string_and_rejects_non_positive():
    payload = {"sku": "A", "qty": "42"}
    assert quote_order(payload) == place_order(payload)
    for bad in (0, -5, "0"):
        with pytest.raises(ValueError):
            place_order({"sku": "A", "qty": bad})
        with pytest.raises(ValueError):
            quote_order({"sku": "A", "qty": bad})
```

Net change: extract one shared helper in one source file, plus one
regression test. That's the `expected_diff_lines_p50` envelope this mission
is scored against.

## What the agent got wrong

Two red flags a supervisor should catch on the agent's diff:

1. **It de-duplicated by deleting behaviour.** The agent removed the model
   call from `quote_order` instead of factoring it out. The raw
   `int(payload["qty"])` read skips the `qty > 0` rule entirely, so a quote
   for `qty=0` prices `0` while the matching placement raises — the two paths
   now disagree on every non-positive input.
2. **It kept two pricing paths.** The whole problem is that pricing happens
   two different ways. "DRYing" one path by gutting it leaves the second path
   subtly different forever. The fix is *one* path both functions call.

## What a strong supervisor would have prompted

- *"`place_order` rejects `qty <= 0`. Does `quote_order` after your change?
  Show me a quote and a placement for `qty=0`."* — surfaces the dropped rule.
- *"De-duplicate by extracting a shared helper both call, not by removing
  validation from one path."*
- *"Add a regression test that feeds a string `qty` and a non-positive `qty`
  and asserts the two paths agree (and reject the same input)."*

## Validators a careless patch would still trip

- `regression_test_required` — no new test mentioning `quote_order` /
  `place_order` / `coercion` / `qty`.
- `forbidden_changes.weakened_visible_test` — fires on any patch that edits
  the visible integer-qty expectations to match a broken total.
- `forbidden_changes.module_rewritten` — fires on a wholesale rewrite of
  `orders_api.py` instead of the small extract-a-helper change.
