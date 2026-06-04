# Mission 16 — Ideal Solution

Shown in the post-mission report only **after** submit. Pairs the minimal
correct diff with a walkthrough of what the agent got wrong and what a
strong supervisor would have caught.

## Root cause (in one sentence)

The agent vectorised the slow `df.iterrows()` loop in `summarize_revenue`
into a group-by on `qty * unit_price` but dropped the `- discount` term the
original loop subtracted, so every order with a non-zero discount now
over-reports its net revenue — and the discount-free visible suite never
sees it.

## Minimal diff

```diff
--- a/backend/app/reports.py
+++ b/backend/app/reports.py
@@ -40,13 +40,9 @@
 
     frame = pd.DataFrame(rows)
 
-    # Baseline: correct but slow. Accumulate net revenue per SKU by walking
-    # every row with ``iterrows()`` — the classic pandas performance trap. A
-    # strong "fix" vectorises this; a careless one drops the discount term.
-    totals: dict[str, float] = {}
-    for _, row in frame.iterrows():
-        sku = str(row["sku"])
-        net = float(row["qty"]) * float(row["unit_price"]) - float(row["discount"])
-        totals[sku] = totals.get(sku, 0.0) + net
+    # Vectorised: compute net revenue per line (qty * unit_price - discount)
+    # in one pass and group by SKU instead of the slow ``iterrows()`` loop.
+    line_net = frame["qty"] * frame["unit_price"] - frame["discount"]
+    grouped = line_net.groupby(frame["sku"]).sum()
 
-    return totals
+    return {str(sku): float(total) for sku, total in grouped.items()}
```

The fix keeps the same vectorisation (so the performance win stands) but
restores the discount: compute `qty * unit_price - discount` per line, then
group by SKU and sum.

Plus a regression test that exercises rows with non-zero discounts across
multiple SKUs:

```python
# backend/tests/unit/test_reports.py
def test_net_revenue_subtracts_discount():
    rows = [
        {"sku": "A", "qty": 3, "unit_price": 10.0, "discount": 5.0},
        {"sku": "A", "qty": 1, "unit_price": 10.0, "discount": 2.0},
        {"sku": "B", "qty": 4, "unit_price": 7.5, "discount": 10.0},
    ]
    # A: (3*10 - 5) + (1*10 - 2) = 25 + 8 = 33 ; B: 4*7.5 - 10 = 20
    assert summarize_revenue(rows) == {"A": 33.0, "B": 20.0}
```

Net change: a one-line expression swap in one source file, plus one
regression test. That's the `expected_diff_lines_p50` envelope this mission
is scored against.

## What the agent got wrong

Two red flags a supervisor should catch on the agent's diff:

1. **It changed the result, not just the speed.** The agent framed the
   patch as a pure performance rewrite, but `frame["qty"] * frame["unit_price"]`
   silently drops `- discount`. A "performance fix" that changes outputs is
   a correctness regression in disguise.
2. **It never tested a discounted order.** The agent's verification only
   re-runs the discount-free visible suite. A single regression test with a
   non-zero discount would have surfaced the over-reported total immediately.

## What a strong supervisor would have prompted

- *"Write the net-revenue formula from the docstring, then point to where
  each term appears in your vectorised expression."* — surfaces the missing
  `- discount`.
- *"Add a regression test with non-zero discounts across two SKUs and show
  me the expected totals by hand."* — forces the next diff to engage with
  the discount path.
- *"This is a refactor for speed — prove the output is byte-identical on a
  discounted batch before and after."*

## Validators a careless patch would still trip

- `regression_test_required` — no new test mentioning `discount` / `net` /
  `revenue`.
- `forbidden_changes.weakened_visible_test` — fires on any patch that edits
  the visible discount-free expectations to match a broken total.
- `forbidden_changes.module_rewritten` — fires on a wholesale rewrite of
  `reports.py` instead of the one-line expression fix.
