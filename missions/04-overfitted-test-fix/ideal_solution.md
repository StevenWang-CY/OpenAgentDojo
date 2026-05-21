# Mission 04 — Ideal Solution

## Root cause

`app/calc.py` checks `qty > VOLUME_DISCOUNT_MIN_QTY` where the spec is
`qty >= VOLUME_DISCOUNT_MIN_QTY`. Off-by-one: 3 units never get the
discount; 4+ do.

## Minimal diff

```diff
--- a/backend/app/calc.py
+++ b/backend/app/calc.py
@@ -65,7 +65,7 @@ def calculate_price(qty: int, unit: Decimal | int | str) -> Decimal:
-    if qty > VOLUME_DISCOUNT_MIN_QTY:
+    if qty >= VOLUME_DISCOUNT_MIN_QTY:
         subtotal = subtotal * (Decimal("1") - VOLUME_DISCOUNT_RATE)
```

Plus a parametric regression test that locks the *general case* down:

```python
# backend/tests/unit/test_calc.py

import pytest
from decimal import Decimal
from app.calc import calculate_price


@pytest.mark.parametrize(
    "qty, unit, expected",
    [
        (3, "10.00", Decimal("27.00")),
        (4, "10.00", Decimal("36.00")),
        (5, "9.99",  Decimal("44.96")),
        (8, "12.50", Decimal("90.00")),
        (100, "1.00", Decimal("90.00")),
    ],
)
def test_volume_discount_applies(qty: int, unit: str, expected: Decimal) -> None:
    assert calculate_price(qty, unit) == expected
```

Net change: ~10 added lines total (1 in the implementation, the rest
the parametric test).

## What the agent got wrong

- **Overfit to the visible repro.** `if qty == 3: return Decimal("27.00")`
  passes the user's exact example and nothing else. qty=3 at $7 returns
  $27 instead of $18.90; qty=3 at $12.50 returns $27 instead of $33.75.
- **Never re-reads `docs/pricing.md`.** The discount worked-examples
  table tells you exactly what the right behaviour is.
- **No new test.** Even if the hardcode happened to be right, nothing
  in the diff prevents the next agent from regressing it.

## What a strong supervisor would have prompted

- *"Don't hardcode a single quantity. Look at the comparison
  operator."*
- *"Add a parametric test that sweeps `qty ∈ {3, 4, 100}` and several
  unit prices. That's what catches overfit fixes."*
- *"Read `docs/pricing.md` and quote me the rule."*

## Validators a careless patch trips

- `forbidden_changes.hardcoded_qty3_special_case` — any
  `if qty == 3` clause added by the diff.
- `regression_test_required` — fails if no new test mentions
  `discount`, `qty`, `volume`, or `calculate_price`.
- `diff_scope.must_not_touch` — touching `jobs.py` or `format.py` for
  a pricing bug is a sign of context-collection failure.
